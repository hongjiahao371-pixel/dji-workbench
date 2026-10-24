#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DJI 素材工作台（dji-workbench）

面向 DJI Osmo Pocket 4 Pro / Osmo Action 6 等支持 SMB 自动上传的相机：
相机「充电 + 连 Wi-Fi」时自动把素材经 SMB 传到 NAS 的 `DJI Device/<相机名>/<日期>/`
目录树，本工具监听该目录完成入库，并提供素材库浏览/挑选/视频代理/DNG 预览/SD 卡导入。

启动：python3 -m app.web_app
环境变量：DJI_WB_DATA（数据目录）、DJI_WB_ROOTS（冒号分隔的监听根，追加到配置）、
         DJI_WB_PORT、DJI_WB_HOST
"""
import io
import json
import logging
import mimetypes
import os
import queue
import re
import shutil
import sqlite3
import struct
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file
from PIL import Image, ImageDraw, ImageOps

log = logging.getLogger("dji-wb")

APP_VERSION = "0.1.0"
VIDEO_EXTS = {".mp4", ".mov"}
RAW_EXTS = {".dng"}
IMG_EXTS = {".jpg", ".jpeg"}
SIDECAR_EXTS = {".srt"}
MEDIA_EXTS = VIDEO_EXTS | RAW_EXTS | IMG_EXTS | SIDECAR_EXTS
PANO_RATIO = 2.2          # 宽高比超过此值按全景处理
SKIP_SUFFIXES = (".tmp", ".part", ".partial", ".download", ".crdownload")

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8920,
    "watch_roots": [],        # 相机 SMB 自动上传落盘的根目录，如 /media/inbox/DJI Device
    "poll_interval": 20,      # 扫描周期（秒）
    "stable_age": 10,         # mtime 距今超过该秒数才允许判稳定
    "stable_scans": 2,        # 连续 N 次扫描 size 不变才判稳定
    "proxy_enabled": True,
    "proxy_height": 1080,
    "proxy_crf": 23,
    "lut_path": "",           # .cube LUT，套在代理转码上（如 D-Log M -> Rec.709）
    "thumb_size": 640,
    "backup_dir": "",         # 可选：入库后自动复制一份到该目录（素材安全冗余）
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    root TEXT DEFAULT '',
    rel_path TEXT DEFAULT '',
    device TEXT DEFAULT '',
    day_folder TEXT DEFAULT '',
    kind TEXT DEFAULT 'other',
    ext TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0,
    taken_at TEXT DEFAULT '',
    sha256 TEXT DEFAULT '',
    duplicate_of INTEGER,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    duration REAL DEFAULT 0,
    codec TEXT DEFAULT '',
    gps_lat REAL,
    gps_lon REAL,
    rating INTEGER DEFAULT 0,
    rejected INTEGER DEFAULT 0,
    stable INTEGER DEFAULT 0,
    scans_stable INTEGER DEFAULT 0,
    last_size INTEGER DEFAULT -1,
    missing INTEGER DEFAULT 0,
    thumb_status TEXT DEFAULT 'none',
    thumb_note TEXT DEFAULT '',
    proxy_status TEXT DEFAULT 'none',
    proxy_note TEXT DEFAULT '',
    source TEXT DEFAULT 'watch',
    created_at REAL DEFAULT 0,
    updated_at REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);
CREATE INDEX IF NOT EXISTS idx_items_stable ON items(stable);
CREATE INDEX IF NOT EXISTS idx_items_taken ON items(taken_at);
CREATE INDEX IF NOT EXISTS idx_items_sha ON items(sha256);
"""

ITEM_COLS = ["id", "path", "root", "rel_path", "device", "day_folder", "kind", "ext",
             "size", "mtime", "taken_at", "sha256", "duplicate_of", "width", "height",
             "duration", "codec", "gps_lat", "gps_lon", "rating", "rejected", "stable",
             "thumb_status", "thumb_note", "proxy_status", "proxy_note", "source",
             "created_at", "updated_at"]


def now(): return time.time()


def taken_at_str(day: str, mtime: float) -> str:
    """优先用相机落盘的日期文件夹（相机时区），时间部分取文件 mtime。"""
    d = day if re.match(r"\d{4}-\d{2}-\d{2}", day or "") else datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    t = datetime.fromtimestamp(mtime).strftime("%H:%M")
    return f"{d} {t}"


def load_config(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = data_dir / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    if cfg_path.exists():
        try:
            cfg.update(json.loads(cfg_path.read_text(encoding="utf-8")))
        except Exception as e:  # 配置损坏时用默认值兜底
            log.warning("config.json 解析失败，使用默认配置: %s", e)
    env_roots = os.environ.get("DJI_WB_ROOTS", "")
    if env_roots:
        for r in env_roots.split(":"):
            if r and r not in cfg["watch_roots"]:
                cfg["watch_roots"].append(r)
    if os.environ.get("DJI_WB_PORT"):
        cfg["port"] = int(os.environ["DJI_WB_PORT"])
    if os.environ.get("DJI_WB_HOST"):
        cfg["host"] = os.environ["DJI_WB_HOST"]
    return cfg


def save_config(data_dir: Path, cfg: dict):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / "config.json.tmp"
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, data_dir / "config.json")  # 原子写


def classify(ext: str) -> str:
    ext = ext.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in RAW_EXTS:
        return "raw"
    if ext in IMG_EXTS:
        return "photo"
    if ext in SIDECAR_EXTS:
        return "sidecar"
    return "other"


# ---------------------------------------------------------------- 文件完整性

def looks_complete(path: str, ext: str) -> bool:
    """轻量完整性判断：配合「size 连续多次不变」共同判定上传完成。"""
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return False
        ext = ext.lower()
        with open(path, "rb") as f:
            head = f.read(64)
            if ext in VIDEO_EXTS:
                if b"ftyp" not in head[:16]:
                    return False
                tail_win = min(size, 4 * 1024 * 1024)
                f.seek(size - tail_win)
                tail = f.read(tail_win)
                head_win = head if size <= 4 * 1024 * 1024 else b""
                if b"moov" not in tail and b"moov" not in head_win:
                    # moov 在写完前通常最后落盘，找不到视为仍在上传
                    return False
                return True
            if ext in IMG_EXTS:
                if not head.startswith(b"\xff\xd8"):
                    return False
                f.seek(max(0, size - 2))
                return f.read(2) == b"\xff\xd9"
            if ext in RAW_EXTS:
                return head[:2] in (b"II", b"MM")
            if ext == ".srt":
                return True
            return True
    except OSError:
        return False


def sha256_file(path: str, chunk=1024 * 1024) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------- 媒体探测

def probe_image(path: str):
    """返回 (width, height, gps_lat, gps_lon)"""
    try:
        with Image.open(path) as im:
            w, h = im.size
            lat = lon = None
            try:
                exif = im.getexif()
                gps = exif.get_ifd(0x8825)  # GPSInfo IFD
                if gps:
                    def dms(v):
                        return float(v[0]) + float(v[1]) / 60.0 + float(v[2]) / 3600.0
                    lat_ref = gps.get(1, "N")
                    lon_ref = gps.get(3, "E")
                    if gps.get(2) and gps.get(4):
                        lat = dms(gps[2])
                        lon = dms(gps[4])
                        if str(lat_ref).upper() == "S":
                            lat = -lat
                        if str(lon_ref).upper() == "W":
                            lon = -lon
            except Exception:
                pass
            return w, h, lat, lon
    except Exception:
        return 0, 0, None, None


def ffprobe_json(path: str) -> dict:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, timeout=120)
        return json.loads(out.stdout.decode("utf-8", "replace") or "{}")
    except Exception:
        return {}


def probe_video(path: str):
    """返回 (width, height, duration, codec, gps_lat, gps_lon)"""
    j = ffprobe_json(path)
    w = h = 0
    dur = 0.0
    codec = ""
    for s in j.get("streams", []):
        if s.get("codec_type") == "video":
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
            codec = s.get("codec_name") or ""
            break
    try:
        dur = float(j.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    lat, lon = probe_gps_mp4(path)
    return w, h, dur, codec, lat, lon


GPS_RE = re.compile(rb"([+-]\d{1,3}\.\d{1,6})([+-]\d{1,3}\.\d{1,6})")


def probe_gps_mp4(path: str, window=8 * 1024 * 1024):
    """DJI 相机把 GPS 写在 mp4 的 ©xyz 原子（ISO 6709 文本）。扫头尾窗口找。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            chunks = []
            chunks.append(f.read(min(size, window)))
            if size > window:
                f.seek(size - window)
                chunks.append(f.read(window))
        for c in chunks:
            i = c.find(b"\xa9xyz")
            if i >= 0:
                m = GPS_RE.search(c[i:i + 512])
                if m:
                    lat = float(m.group(1))
                    lon = float(m.group(2))
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        return lat, lon
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------- DNG 内嵌预览提取

_TIFF_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


def extract_dng_preview(path: str):
    """解析 TIFF/DNG IFD，取出最大的内嵌 JPEG 预览（DNG 直解 16-bit Bayer 会偏色/压黑，
    用内嵌预览做库内浏览是稳妥路线）。找不到返回 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if head[:2] == b"II":
                en = "<"
            elif head[:2] == b"MM":
                en = ">"
            else:
                return None
            best = None  # (area, blob)

            def read_blob(off, ln):
                if ln <= 0 or ln > 256 * 1024 * 1024:
                    return None
                f.seek(off)
                data = f.read(ln)
                return data if data[:2] == b"\xff\xd8" else None

            def handle_ifd(off, depth=0):
                nonlocal best
                if depth > 6 or off <= 0:
                    return
                f.seek(off)
                raw = f.read(2)
                if len(raw) < 2:
                    return
                n = struct.unpack(en + "H", raw)[0]
                tags = {}
                for _ in range(n):
                    e = f.read(12)
                    if len(e) < 12:
                        return
                    tag, typ, cnt = struct.unpack(en + "HHI", e[:8])
                    tags[tag] = (typ, cnt, e[8:12])
                nxt = struct.unpack(en + "I", f.read(4))[0]

                def one(tag):
                    if tag not in tags:
                        return None
                    typ, cnt, val = tags[tag]
                    if cnt == 1 and typ in (3, 4):
                        fmt = en + ("H" if typ == 3 else "I")
                        return struct.unpack(fmt, val[:struct.calcsize(fmt)])[0]
                    if cnt == 1 and typ == 16:  # RATIONAL（如分辨率）不关心
                        return None
                    return None

                def arr(tag):
                    if tag not in tags:
                        return []
                    typ, cnt, val = tags[tag]
                    size = _TIFF_TYPE_SIZE.get(typ, 1) * cnt
                    if size <= 4:
                        data = val[:size]
                    else:
                        off2 = struct.unpack(en + "I", val)[0]
                        f.seek(off2)
                        data = f.read(size)
                    fmt = {3: "H", 4: "I"}.get(typ)
                    if not fmt or not data:
                        return []
                    try:
                        return list(struct.unpack(en + str(cnt) + fmt, data))
                    except struct.error:
                        return []

                # IFD0 自带缩略图 JPEG
                if 513 in tags and 514 in tags:
                    blob = read_blob(one(513), one(514))
                    if blob:
                        best = max(best or (0, b""), (len(blob), blob), key=lambda x: x[0])
                comp = one(259)
                if comp in (6, 7):
                    offs = arr(273)
                    cnts = arr(279)
                    if offs and cnts and len(offs) == len(cnts):
                        blob = b"".join(read_blob(o, c) or b"" for o, c in zip(offs, cnts))
                        if blob[:2] == b"\xff\xd8":
                            best = max(best or (0, b""), (len(blob), blob), key=lambda x: x[0])
                if depth < 5:
                    for sub in arr(330):
                        handle_ifd(sub, depth + 1)
                if 0 < nxt < 2 ** 31 and nxt != off:
                    handle_ifd(nxt, depth + 1)

            handle_ifd(struct.unpack(en + "I", head[4:8])[0])
            return best[1] if best else None
    except Exception:
        return None


def _placeholder_thumb(text: str, size: int) -> Image.Image:
    im = Image.new("RGB", (480, 300), (24, 29, 38))
    d = ImageDraw.Draw(im)
    d.text((im.width // 2 - len(text) * 7, im.height // 2 - 12), text, fill=(150, 160, 175))
    return im


# ---------------------------------------------------------------- 应用上下文

class WB:
    def __init__(self, cfg: dict, data_dir: Path, start_workers=False):
        self.cfg = cfg
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "workbench.db"
        self.thumb_dir = self.data_dir / "thumbs"
        self.poster_dir = self.data_dir / "posters"
        self.proxy_dir = self.data_dir / "proxies"
        for d in (self.data_dir, self.thumb_dir, self.poster_dir, self.proxy_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.has_ffmpeg = bool(shutil.which("ffmpeg"))
        self.has_ffprobe = bool(shutil.which("ffprobe"))
        self.hash_q = queue.Queue()
        self.thumb_q = queue.Queue()
        self.proxy_q = queue.Queue()
        self.scanning = threading.Event()
        self.last_scan = 0.0
        self.wake = threading.Event()
        self.threads = []
        if start_workers:
            self.start_workers()

    # ---- DB
    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        return conn

    # ---- 扫描入库
    def scan_once(self) -> dict:
        self.scanning.set()
        stats = {"roots": 0, "new": 0, "stable": 0, "missing": 0}
        try:
            conn = self.db()
            try:
                seen_paths = set()
                for root in self.cfg["watch_roots"]:
                    root = str(root)
                    if not root or not os.path.isdir(root):
                        continue
                    stats["roots"] += 1
                    for dirpath, dirnames, filenames in os.walk(root):
                        dirnames.sort()
                        for fn in sorted(filenames):
                            if fn.startswith((".", "._")) or fn.endswith(SKIP_SUFFIXES):
                                continue
                            ext = os.path.splitext(fn)[1].lower()
                            if ext not in MEDIA_EXTS:
                                continue
                            p = os.path.join(dirpath, fn)
                            try:
                                st = os.stat(p)
                            except OSError:
                                continue
                            seen_paths.add(p)
                            rel = os.path.relpath(p, root)
                            parts = rel.split(os.sep)
                            device = parts[0] if len(parts) > 2 else ""
                            day = parts[1] if len(parts) > 2 else (parts[0] if len(parts) == 2 else "")
                            if not re.match(r"\d{4}-\d{2}-\d{2}", day):
                                day = ""
                            row = conn.execute("SELECT id, size, stable, scans_stable, last_size FROM items WHERE path=?",
                                               (p,)).fetchone()
                            tk = taken_at_str(day, st.st_mtime)
                            if row is None:
                                conn.execute(
                                    "INSERT INTO items(path,root,rel_path,device,day_folder,kind,ext,size,mtime,"
                                    "taken_at,stable,scans_stable,last_size,source,created_at,updated_at)"
                                    " VALUES(?,?,?,?,?,?,?,?,?,?,0,0,?,'watch',?,?)",
                                    (p, root, rel, device, day, classify(ext), ext,
                                     st.st_size, st.st_mtime, tk, st.st_size, now(), now()))
                                stats["new"] += 1
                            elif not row["stable"]:
                                if row["last_size"] == st.st_size:
                                    scans = row["scans_stable"] + 1
                                else:
                                    scans = 0
                                stable = 0
                                if scans >= int(self.cfg["stable_scans"]) and \
                                        (now() - st.st_mtime) > float(self.cfg["stable_age"]) and \
                                        looks_complete(p, ext):
                                    stable = 1
                                    stats["stable"] += 1
                                conn.execute("UPDATE items SET size=?, mtime=?, taken_at=?, scans_stable=?, stable=?, "
                                             "last_size=?, missing=0, updated_at=? WHERE id=?",
                                             (st.st_size, st.st_mtime, tk, scans, stable, st.st_size, now(), row["id"]))
                            else:
                                conn.execute("UPDATE items SET size=?, mtime=?, taken_at=?, missing=0, updated_at=? WHERE id=?",
                                             (st.st_size, st.st_mtime, tk, now(), row["id"]))
                # 磁盘上已消失的条目标记 missing（不删，方便回溯）。
                # 监听根下的按本轮 walk 结果判定；导入来源的文件不在监听根里，单独做存在性检查。
                for r in conn.execute("SELECT id, path FROM items WHERE missing=0 AND source='watch'").fetchall():
                    if r["path"] not in seen_paths:
                        conn.execute("UPDATE items SET missing=1, updated_at=? WHERE id=?", (now(), r["id"]))
                        stats["missing"] += 1
                for r in conn.execute("SELECT id, path FROM items WHERE missing=0 AND source='import'").fetchall():
                    if not os.path.exists(r["path"]):
                        conn.execute("UPDATE items SET missing=1, updated_at=? WHERE id=?", (now(), r["id"]))
                        stats["missing"] += 1
                conn.commit()
            finally:
                conn.close()
            self.enqueue_pending()
        finally:
            self.scanning.clear()
            self.last_scan = now()
        return stats

    def enqueue_pending(self):
        """把稳定条目里缺哈希/缺缩略图/缺代理的分派到各串行队列。"""
        conn = self.db()
        try:
            for r in conn.execute(
                    "SELECT id, kind FROM items WHERE stable=1 AND missing=0").fetchall():
                self._enqueue_row(conn, r["id"], r["kind"])
        finally:
            conn.close()

    def _enqueue_row(self, conn, item_id, kind):
        row = conn.execute("SELECT sha256, thumb_status, proxy_status FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            return
        if not row["sha256"]:
            self.hash_q.put(item_id)
        if row["thumb_status"] == "none":
            self.thumb_q.put(item_id)
        if kind == "video" and self.cfg.get("proxy_enabled") and row["proxy_status"] == "none":
            self.proxy_q.put(item_id)

    def trigger_scan(self):
        self.wake.set()

    def start_workers(self):
        for target in (self._hash_loop, self._thumb_loop, self._proxy_loop, self._scanner_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self.threads.append(t)

    def _scanner_loop(self):
        while True:
            woke = self.wake.wait(timeout=max(2, float(self.cfg["poll_interval"])))
            self.wake.clear()
            try:
                self.scan_once()
            except Exception:
                log.exception("scan failed")

    # ---- 后台任务
    def _hash_loop(self):
        while True:
            item_id = self.hash_q.get()
            try:
                self.hash_one(item_id)
            except Exception:
                log.exception("hash failed id=%s", item_id)
            finally:
                self.hash_q.task_done()

    def hash_one(self, item_id):
        conn = self.db()
        try:
            r = conn.execute("SELECT path, sha256 FROM items WHERE id=?", (item_id,)).fetchone()
            if not r or r["sha256"] or not os.path.isfile(r["path"]):
                return
            digest = sha256_file(r["path"])
            dup = conn.execute("SELECT id FROM items WHERE sha256=? AND id!=? LIMIT 1",
                               (digest, item_id)).fetchone()
            conn.execute("UPDATE items SET sha256=?, duplicate_of=?, updated_at=? WHERE id=?",
                         (digest, dup["id"] if dup else None, now(), item_id))
            conn.commit()
        finally:
            conn.close()

    def _thumb_loop(self):
        while True:
            item_id = self.thumb_q.get()
            try:
                self.thumb_one(item_id)
            except Exception:
                log.exception("thumb failed id=%s", item_id)
            finally:
                self.thumb_q.task_done()

    def _set_thumb(self, conn, item_id, status, note=""):
        conn.execute("UPDATE items SET thumb_status=?, thumb_note=?, updated_at=? WHERE id=?",
                     (status, note, now(), item_id))
        conn.commit()

    def thumb_one(self, item_id):
        conn = self.db()
        try:
            r = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if not r or r["thumb_status"] not in ("none", "failed"):
                return
            if not os.path.isfile(r["path"]):
                self._set_thumb(conn, item_id, "failed", "文件不存在")
                return
            self._set_thumb(conn, item_id, "generating")
            kind = r["kind"]
            width = r["width"] or 0
            height = r["height"] or 0
            try:
                if kind in ("photo", "pano"):
                    w, h, lat, lon = probe_image(r["path"])
                    kind = "pano" if (w and h and w / max(h, 1) >= PANO_RATIO) else "photo"
                    im = self._thumb_from_image(r["path"], self.cfg["thumb_size"])
                    conn.execute("UPDATE items SET width=?, height=?, gps_lat=?, gps_lon=?, kind=? WHERE id=?",
                                 (w, h, lat, lon, kind, item_id))
                elif kind == "raw":
                    blob = extract_dng_preview(r["path"])
                    if blob:
                        im = self._thumb_from_blob(blob, self.cfg["thumb_size"])
                    else:
                        im = _placeholder_thumb("DNG RAW", self.cfg["thumb_size"])
                elif kind == "video":
                    if not self.has_ffmpeg:
                        im = _placeholder_thumb("VIDEO", self.cfg["thumb_size"])
                    else:
                        im = self._poster_from_video(r["path"])
                        w, h, dur, codec, lat, lon = probe_video(r["path"])
                        conn.execute("UPDATE items SET width=?, height=?, duration=?, codec=?, gps_lat=?, gps_lon=? "
                                     "WHERE id=?", (w, h, dur, codec, lat, lon, item_id))
                else:
                    self._set_thumb(conn, item_id, "ready", "非媒体文件，仅索引")
                    return
                dest = self.thumb_dir / f"{item_id}.jpg"
                tmp = str(dest) + ".tmp"
                im.save(tmp, "JPEG", quality=85)
                os.replace(tmp, dest)
                self._set_thumb(conn, item_id, "ready")
            except Exception as e:
                self._set_thumb(conn, item_id, "failed", str(e)[:200])
        finally:
            conn.close()

    def _thumb_from_image(self, path, size):
        im = Image.open(path)
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size))
        return im

    def _thumb_from_blob(self, blob, size):
        im = Image.open(io.BytesIO(blob))
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((size, size))
        return im

    def _poster_from_video(self, path):
        out = os.path.join(self.poster_dir, f"poster_tmp_{os.getpid()}.jpg")
        # 先快速 seek 到 1s 抽帧；短视频（或 seek 过 EOF）退回首帧重试
        for seek in ("1", None):
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
            if seek:
                cmd += ["-ss", seek]
            cmd += ["-i", path, "-frames:v", "1",
                    "-vf", "scale=480:-2:flags=bicubic", out]
            subprocess.run(cmd, capture_output=True, timeout=300)
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                break
        if not os.path.isfile(out) or os.path.getsize(out) == 0:
            raise RuntimeError("ffmpeg 抽帧失败")
        with Image.open(out) as im:
            im = im.convert("RGB")
        os.remove(out)
        return im

    def _proxy_loop(self):
        while True:
            item_id = self.proxy_q.get()
            try:
                self.proxy_one(item_id)
            except Exception:
                log.exception("proxy failed id=%s", item_id)
            finally:
                self.proxy_q.task_done()

    def proxy_one(self, item_id):
        conn = self.db()
        try:
            r = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if not r or r["kind"] != "video" or r["proxy_status"] in ("ready", "generating"):
                return
            if not self.has_ffmpeg:
                conn.execute("UPDATE items SET proxy_status='failed', proxy_note='ffmpeg 不可用', updated_at=? WHERE id=?",
                             (now(), item_id))
                conn.commit()
                return
            conn.execute("UPDATE items SET proxy_status='generating', updated_at=? WHERE id=?", (now(), item_id))
            conn.commit()
            dst = self.proxy_dir / f"{item_id}.mp4"
            tmp = str(dst) + ".tmp.mp4"
            try:
                self._transcode(r["path"], tmp)
                os.replace(tmp, dst)
                conn.execute("UPDATE items SET proxy_status='ready', proxy_note='', updated_at=? WHERE id=?",
                             (now(), item_id))
            except Exception as e:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                conn.execute("UPDATE items SET proxy_status='failed', proxy_note=?, updated_at=? WHERE id=?",
                             (str(e)[:200], now(), item_id))
            conn.commit()
        finally:
            conn.close()

    def _transcode(self, src, dst):
        vf = []
        lut = self.cfg.get("lut_path") or ""
        if lut and os.path.isfile(lut):
            esc = lut.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            vf.append(f"lut3d='{esc}'")
        h = int(self.cfg.get("proxy_height") or 1080)
        vf.append(f"scale=-2:'min(ih,{h})':flags=bicubic")
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
               "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast",
               "-crf", str(self.cfg.get("proxy_crf") or 23), "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dst]
        subprocess.run(cmd, capture_output=True, timeout=3600)
        if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            raise RuntimeError("代理转码失败")

    # ---- 统计
    def queue_depths(self):
        return {"hash": self.hash_q.qsize(), "thumb": self.thumb_q.qsize(), "proxy": self.proxy_q.qsize()}


# ---------------------------------------------------------------- Flask

def create_app(wb: WB):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    def item_row(r):
        d = {c: r[c] for c in ITEM_COLS if c in r.keys()}
        d["thumb_url"] = f"/api/items/{r['id']}/thumb" if r["thumb_status"] == "ready" and r["kind"] != "sidecar" else ""
        d["proxy_url"] = f"/api/items/{r['id']}/proxy" if r["proxy_status"] == "ready" else ""
        d["pano"] = 1 if (r["kind"] == "pano" or (r["width"] and r["height"] and r["width"] / max(r["height"], 1) >= PANO_RATIO)) else 0
        return d

    @app.route("/")
    def index():
        return send_file(os.path.join(os.path.dirname(__file__), "static", "index.html"))

    @app.route("/api/health")
    def health():
        conn = wb.db()
        try:
            total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        finally:
            conn.close()
        return jsonify({"ok": True, "version": APP_VERSION, "items": total,
                        "ffmpeg": wb.has_ffmpeg, "ffprobe": wb.has_ffprobe,
                        "roots": wb.cfg["watch_roots"], "last_scan": wb.last_scan,
                        "queues": wb.queue_depths()})

    @app.route("/api/stats")
    def stats():
        conn = wb.db()
        try:
            by_kind = {r["kind"]: r["c"] for r in conn.execute(
                "SELECT kind, COUNT(*) c FROM items WHERE missing=0 GROUP BY kind")}
            devices = [{"name": r["device"] or "(未分设备)", "count": r["c"], "size": r["s"] or 0}
                       for r in conn.execute(
                           "SELECT device, COUNT(*) c, SUM(size) s FROM items WHERE missing=0 AND kind!='sidecar' "
                           "GROUP BY device ORDER BY c DESC")]
            total_size = conn.execute("SELECT COALESCE(SUM(size),0) s FROM items WHERE missing=0").fetchone()["s"]
            dates = [r["d"] for r in conn.execute(
                "SELECT DISTINCT substr(taken_at,1,10) d FROM items WHERE missing=0 AND taken_at!='' ORDER BY d DESC LIMIT 400")]
        finally:
            conn.close()
        return jsonify({"by_kind": by_kind, "devices": devices, "total_size": total_size,
                        "dates": dates, "queues": wb.queue_depths()})

    @app.route("/api/items")
    def list_items():
        conn = wb.db()
        try:
            where = ["missing=0", "kind != 'other'"]
            args = []
            q = request.args
            kind = q.get("kind", "")
            if kind in ("photo", "video", "raw", "pano", "sidecar"):
                where.append("kind=?")
                args.append(kind)
            device = q.get("device", "")
            if device:
                where.append("device=?")
                args.append(device)
            date = q.get("date", "")
            if re.match(r"\d{4}-\d{2}-\d{2}", date):
                where.append("substr(taken_at,1,10)=?")
                args.append(date)
            min_rating = q.get("min_rating")
            if min_rating:
                where.append("rating>=?")
                args.append(int(min_rating))
            rejected = q.get("rejected", "hide")
            if rejected == "only":
                where.append("rejected=1")
            elif rejected != "show":
                where.append("rejected=0")
            if q.get("duplicates", "hide") != "show":
                where.append("duplicate_of IS NULL")
            text = (q.get("q") or "").strip()
            if text:
                where.append("(rel_path LIKE ? OR device LIKE ?)")
                args += [f"%{text}%", f"%{text}%"]
            sort = q.get("sort", "date_desc")
            order = {"date_desc": "taken_at DESC, id DESC", "date_asc": "taken_at ASC, id ASC",
                     "size_desc": "size DESC", "rating_desc": "rating DESC, taken_at DESC"}.get(sort, "taken_at DESC, id DESC")
            page = max(1, int(q.get("page", 1) or 1))
            per_page = min(200, max(1, int(q.get("per_page", 60) or 60)))
            wsql = " AND ".join(where)
            total = conn.execute(f"SELECT COUNT(*) c FROM items WHERE {wsql}", args).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM items WHERE {wsql} ORDER BY {order} LIMIT ? OFFSET ?",
                args + [per_page, (page - 1) * per_page]).fetchall()
        finally:
            conn.close()
        return jsonify({"total": total, "page": page, "per_page": per_page,
                        "items": [item_row(r) for r in rows]})

    @app.route("/api/items/<int:item_id>")
    def get_item(item_id):
        conn = wb.db()
        try:
            r = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if not r:
                abort(404)
            d = item_row(r)
            stem = os.path.splitext(os.path.basename(r["path"]))[0]
            sidecars = [x["path"] for x in conn.execute(
                "SELECT path FROM items WHERE kind='sidecar' AND path LIKE ?",
                (os.path.dirname(r["path"]) + os.sep + stem + ".%",))]
            d["sidecars"] = sidecars
        finally:
            conn.close()
        return jsonify(d)

    @app.route("/api/items/<int:item_id>/rate", methods=["POST"])
    def rate_item(item_id):
        body = request.get_json(silent=True) or {}
        sets, args = [], []
        if "rating" in body:
            sets.append("rating=?")
            args.append(max(0, min(5, int(body["rating"] or 0))))
        if "rejected" in body:
            sets.append("rejected=?")
            args.append(1 if body["rejected"] else 0)
        if not sets:
            return jsonify({"error": "nothing to update"}), 400
        sets.append("updated_at=?")
        args += [now(), item_id]
        conn = wb.db()
        try:
            cur = conn.execute(f"UPDATE items SET {', '.join(sets)} WHERE id=?", args)
            conn.commit()
            if cur.rowcount == 0:
                abort(404)
        finally:
            conn.close()
        return jsonify({"ok": True})

    def _serve_item_file(item_id, sub):
        conn = wb.db()
        try:
            r = conn.execute("SELECT path, ext, thumb_status, proxy_status FROM items WHERE id=?",
                             (item_id,)).fetchone()
        finally:
            conn.close()
        if not r:
            abort(404)
        if sub == "file":
            path = r["path"]
        elif sub == "thumb":
            path = str(wb.thumb_dir / f"{item_id}.jpg")
        elif sub == "poster":
            path = str(wb.poster_dir / f"{item_id}.jpg")
        elif sub == "proxy":
            if r["proxy_status"] != "ready":
                return jsonify({"error": "proxy not ready", "status": r["proxy_status"]}), 409
            path = str(wb.proxy_dir / f"{item_id}.mp4")
        else:
            abort(404)
        if not os.path.isfile(path):
            return jsonify({"error": "file missing"}), 404
        mt = mimetypes.guess_type(path)[0] or "application/octet-stream"
        resp = send_file(path, mimetype=mt, conditional=True)
        if sub in ("thumb", "poster"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @app.route("/api/items/<int:item_id>/thumb")
    def serve_thumb(item_id):
        return _serve_item_file(item_id, "thumb")

    @app.route("/api/items/<int:item_id>/poster")
    def serve_poster(item_id):
        return _serve_item_file(item_id, "poster")

    @app.route("/api/items/<int:item_id>/proxy")
    def serve_proxy(item_id):
        return _serve_item_file(item_id, "proxy")

    @app.route("/api/items/<int:item_id>/file")
    def serve_file(item_id):
        return _serve_item_file(item_id, "file")

    # ---- 配置
    @app.route("/api/config", methods=["GET"])
    def get_config():
        safe = {k: v for k, v in wb.cfg.items()}
        return jsonify(safe)

    @app.route("/api/config", methods=["POST"])
    def set_config():
        body = request.get_json(silent=True) or {}
        int_keys = {"poll_interval", "stable_age", "stable_scans", "proxy_height", "proxy_crf", "thumb_size"}
        for k in DEFAULT_CONFIG:
            if k not in body:
                continue
            if k == "watch_roots":
                roots = [str(r).strip() for r in body[k] if str(r).strip()]
                wb.cfg["watch_roots"] = roots
            elif k == "lut_path" or k == "backup_dir":
                wb.cfg[k] = str(body[k]).strip()
            elif k == "proxy_enabled":
                wb.cfg[k] = bool(body[k])
            elif k in int_keys:
                try:
                    wb.cfg[k] = max(1, int(body[k]))
                except (TypeError, ValueError):
                    pass
        save_config(wb.data_dir, wb.cfg)
        wb.trigger_scan()
        return jsonify({"ok": True})

    # ---- 手动扫描
    @app.route("/api/scan", methods=["POST"])
    def scan_now():
        s = wb.scan_once()
        return jsonify({"ok": True, **s})

    # ---- SD 卡导入
    @app.route("/api/browse")
    def browse():
        p = request.args.get("path", "/") or "/"
        p = os.path.abspath(p)
        if not os.path.isdir(p):
            return jsonify({"error": "目录不存在"}), 404
        dirs = []
        try:
            for name in sorted(os.listdir(p)):
                full = os.path.join(p, name)
                if name.startswith(".") or not os.path.isdir(full):
                    continue
                dirs.append({"name": name, "path": full})
        except OSError as e:
            return jsonify({"error": str(e)}), 403
        return jsonify({"path": p, "parent": os.path.dirname(p), "dirs": dirs[:400]})

    @app.route("/api/import/scan", methods=["POST"])
    def import_scan():
        body = request.get_json(silent=True) or {}
        p = str(body.get("path") or "").strip()
        if not p or not os.path.isdir(p):
            return jsonify({"error": "目录不存在"}), 404
        conn = wb.db()
        known = {r["path"] for r in conn.execute("SELECT path FROM items")}
        conn.close()
        found, known_count = [], 0
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in sorted(dirnames) if not d.startswith(".")]
            for fn in sorted(filenames):
                if fn.startswith((".", "._")) or fn.endswith(SKIP_SUFFIXES):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in MEDIA_EXTS:
                    continue
                full = os.path.join(dirpath, fn)
                if full in known:
                    known_count += 1
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                found.append({"path": full, "size": st.st_size, "mtime": st.st_mtime,
                              "ext": ext, "kind": classify(ext)})
        return jsonify({"path": p, "candidates": found, "already_known": known_count})

    @app.route("/api/import/commit", methods=["POST"])
    def import_commit():
        body = request.get_json(silent=True) or {}
        paths = body.get("paths") or []
        if not isinstance(paths, list) or not paths:
            return jsonify({"error": "paths 为空"}), 400
        conn = wb.db()
        added = 0
        try:
            for p in paths:
                p = str(p)
                if not os.path.isfile(p):
                    continue
                if conn.execute("SELECT 1 FROM items WHERE path=?", (p,)).fetchone():
                    continue
                ext = os.path.splitext(p)[1].lower()
                st = os.stat(p)
                conn.execute(
                    "INSERT INTO items(path,root,rel_path,device,day_folder,kind,ext,size,mtime,"
                    "taken_at,stable,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,'import',?,?)",
                    (p, os.path.dirname(p), os.path.basename(p), "", "", classify(ext), ext,
                     st.st_size, st.st_mtime, taken_at_str("", st.st_mtime), now(), now()))
                added += 1
            conn.commit()
        finally:
            conn.close()
        wb.enqueue_pending()
        return jsonify({"ok": True, "added": added})

    return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    data_dir = Path(os.environ.get("DJI_WB_DATA", str(Path(__file__).resolve().parent.parent / "data")))
    cfg = load_config(data_dir)
    wb = WB(cfg, data_dir, start_workers=True)
    app = create_app(wb)
    log.info("DJI 素材工作台 v%s -> http://%s:%s  数据目录=%s  监听根=%s",
             APP_VERSION, cfg["host"], cfg["port"], data_dir, cfg["watch_roots"] or "(未配置，请到设置页添加)")
    app.run(host=cfg["host"], port=cfg["port"], threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
