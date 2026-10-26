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
from PIL import Image, ImageDraw, ImageFilter, ImageOps

log = logging.getLogger("dji-wb")

APP_VERSION = "0.4.0"
SPRITE_FRAMES = 12        # 悬停预览雪碧图帧数
_FONT_CANDIDATES = [      # 水印字体探测顺序（需覆盖中文）
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]
VIDEO_EXTS = {".mp4", ".mov"}
RAW_EXTS = {".dng"}
IMG_EXTS = {".jpg", ".jpeg"}
SIDECAR_EXTS = {".srt"}
AUDIO_EXTS = {".aac"}     # Action 6/Pocket 4 无线麦「机身收音备份」会另存独立 .aac 音轨（官方手册 3.2/4.6 节）
MEDIA_EXTS = VIDEO_EXTS | RAW_EXTS | IMG_EXTS | SIDECAR_EXTS | AUDIO_EXTS
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
    "backup_dir": "",         # 可选：稳定入库后自动复制一份到该目录（素材安全冗余）
    "ai_base_url": "",        # OpenAI 兼容多模态 API（用于照片自动打标），如 https://api.minimaxi.com/v1
    "ai_api_key": "",
    "ai_model": "",
    "export_font_path": "",   # 水印字体（需支持中文的 ttf/ttc，留空自动探测）
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

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS album_items (
    album_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    added_at REAL DEFAULT 0,
    PRIMARY KEY (album_id, item_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT DEFAULT '',
    status TEXT DEFAULT 'queued',
    note TEXT DEFAULT '',
    result TEXT DEFAULT '',
    created_at REAL DEFAULT 0,
    updated_at REAL DEFAULT 0
);
"""

ITEM_COLS = ["id", "path", "root", "rel_path", "device", "day_folder", "kind", "ext",
             "size", "mtime", "taken_at", "sha256", "duplicate_of", "width", "height",
             "duration", "codec", "gps_lat", "gps_lon", "rating", "rejected", "stable",
             "thumb_status", "thumb_note", "proxy_status", "proxy_note", "source",
             "quality", "tags", "sprite_status", "created_at", "updated_at"]


def _ensure_columns(conn, table, cols):
    """老库平滑迁移：缺的列 ALTER TABLE 补上。"""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in cols:
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


ITEM_MIGRATIONS = [
    ("quality", "REAL DEFAULT 0"),        # 照片质量分（清晰度+曝光，0-100）
    ("tags", "TEXT DEFAULT ''"),          # AI 打标 JSON 数组
    ("sprite_status", "TEXT DEFAULT 'none'"),  # 悬停雪碧图 none|generating|ready|failed
    ("phash", "TEXT DEFAULT ''"),         # 感知哈希（dhash 64bit 01 串，相似聚类用）
    ("chash", "TEXT DEFAULT ''"),         # 颜色签名（4x4 平均 RGB JSON，颜色门控）
    ("exif", "TEXT DEFAULT ''"),          # 拍摄参数 JSON（照片光圈快门 ISO 焦距；视频帧率码率）
]


def now(): return time.time()


_DJI_NAME_DATE = re.compile(r"DJI_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})", re.I)


def taken_at_str(day: str, mtime: float, path: str = "") -> str:
    """拍摄时间三级来源：①文件名（DJI_YYYYMMDDHHMM…，相机时钟最准）②日期文件夹 ③mtime。
    日期夹两种格式都认：2026-10-25 与 20261025（Pocket 4 Pro 实测为后者）。"""
    m = _DJI_NAME_DATE.search(os.path.basename(path) or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"
    d = ""
    if day:
        if re.match(r"\d{4}-\d{2}-\d{2}", day):
            d = day
        elif re.match(r"\d{8}", day):
            d = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    if not d:
        d = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
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
    if ext in AUDIO_EXTS:
        return "audio"
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

def _num(v):
    """EXIF 数值统一转 float：IFDRational 可直接 float；PIL 从文件读回的 rational 是 (num, den) 元组。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(v[0]) / float(v[1])
        except (TypeError, ValueError, ZeroDivisionError, IndexError):
            return None


def probe_exif_params(im) -> dict:
    """照片拍摄参数：光圈/曝光/ISO/焦距（数值存 JSON，展示层格式化）。
    每个标签独立容错——单个坏标签不能拖垮整组。"""
    out = {}
    try:
        e = im.getexif().get_ifd(0x8769)  # Exif IFD
        v = _num(e.get(0x829D))
        if v:
            out["f_number"] = round(v, 2)
        v = _num(e.get(0x829A))
        if v:
            out["exposure"] = v
        v = e.get(0x8827)
        if v is not None:
            try:
                out["iso"] = int(v)
            except (TypeError, ValueError):
                pass
        v = _num(e.get(0x920A))
        if v:
            out["focal"] = round(v, 1)
    except Exception:
        pass
    return out


def probe_image(path: str):
    """返回 (width, height, gps_lat, gps_lon, exif_params)"""
    try:
        with Image.open(path) as im:
            w, h = im.size
            lat = lon = None
            params = {}
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
                params = probe_exif_params(im)
            except Exception:
                pass
            return w, h, lat, lon, params
    except Exception:
        return 0, 0, None, None, {}


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
    """返回 (width, height, duration, codec, gps_lat, gps_lon, exif_params)
    exif_params 含 fps / bitrate / codec_profile。"""
    j = ffprobe_json(path)
    w = h = 0
    dur = 0.0
    codec = ""
    params = {}
    for st in j.get("streams", []):
        if st.get("codec_type") == "video":
            w = int(st.get("width") or 0)
            h = int(st.get("height") or 0)
            codec = st.get("codec_name") or ""
            rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or ""
            try:
                if rate and "/" in rate:
                    num, den = rate.split("/")
                    if float(den):
                        params["fps"] = round(float(num) / float(den), 3)
            except (ValueError, ZeroDivisionError):
                pass
            if st.get("profile"):
                params["profile"] = st["profile"]
            if st.get("bit_rate"):
                try:
                    params["v_bitrate"] = int(st["bit_rate"])
                except (TypeError, ValueError):
                    pass
            break
    try:
        dur = float(j.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    if j.get("format", {}).get("bit_rate"):
        try:
            params["bitrate"] = int(j["format"]["bit_rate"])
        except (TypeError, ValueError):
            pass
    lat, lon = probe_gps_mp4(path)
    return w, h, dur, codec, lat, lon, params


_ISO6709_DEG = re.compile(rb"^([+-]\d{1,2}(?:\.\d*)?)([+-]\d{1,3}(?:\.\d*)?)([+-]\d+(?:\.\d*)?)?")
_ISO6709_DM = re.compile(rb"^([+-])(\d{2})(\d{2}(?:\.\d*)?)([+-])(\d{3})(\d{2}(?:\.\d*)?)([+-]\d+(?:\.\d*)?)?")
_ISO6709_DMS = re.compile(rb"^([+-])(\d{2})(\d{2})(\d{2}(?:\.\d*)?)([+-])(\d{3})(\d{2})(\d{2}(?:\.\d*)?)([+-]\d+(?:\.\d*)?)?")


def parse_iso6709(raw: bytes):
    """解析 ISO 6709 坐标串，三种格式与 ExifTool ConvertISO6709 对齐：
    +DD.DDD+DDD.DDD / +DDMM.MMM+DDDMM.MMM / +DDMMSS.SSS+DDDMMSS.SSS（均可带高度段）。
    返回 (lat, lon) 或 None。"""
    s = raw.strip(b"\x00/ \t\r\n")
    m = _ISO6709_DEG.match(s)
    if m and b"." in (m.group(1) + m.group(2)):
        return float(m.group(1)), float(m.group(2))
    m = _ISO6709_DM.match(s)
    if m:
        lat = int(m.group(2)) + float(m.group(3)) / 60.0
        lon = int(m.group(5)) + float(m.group(6)) / 60.0
        return (-lat if m.group(1) == b"-" else lat, -lon if m.group(4) == b"-" else lon)
    m = _ISO6709_DMS.match(s)
    if m:
        lat = int(m.group(2)) + int(m.group(3)) / 60.0 + float(m.group(4)) / 3600.0
        lon = int(m.group(6)) + int(m.group(7)) / 60.0 + float(m.group(8)) / 3600.0
        return (-lat if m.group(1) == b"-" else lat, -lon if m.group(5) == b"-" else lon)
    return None


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
                seg = c[i:i + 512]
                # 原子头(4)后可能有 version/flags(4)、语言码(2)等前导，逐偏移试探
                for off in range(4, min(len(seg), 64)):
                    coords = parse_iso6709(seg[off:])
                    if coords and -90 <= coords[0] <= 90 and -180 <= coords[1] <= 180:
                        return coords
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


def dhash(im, size=8) -> str:
    """差异感知哈希：8x8 灰度梯度 → 64bit 01 串。相近画面距离小，跨目录相似聚类用。"""
    g = im.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(g.getdata())
    bits = []
    for y in range(size):
        row = px[y * (size + 1):(y + 1) * (size + 1)]
        bits.extend("1" if row[x] > row[x + 1] else "0" for x in range(size))
    return "".join(bits)


def phash_distance(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a, b) if x != y) if len(a) == len(b) else 64


def chash(im, cells=4):
    """4x4 平均 RGB 颜色签名。dhash 只看亮度结构、对颜色盲，配颜色门控防同构异色误报。"""
    small = im.convert("RGB").resize((cells, cells), Image.LANCZOS)
    return json.dumps(list(small.getdata()))


def chash_distance(a: str, b: str) -> float:
    """平均每通道绝对差（0-255）。同场景 <15，同构异色通常 >40。"""
    try:
        va, vb = json.loads(a), json.loads(b)
        if len(va) != len(vb) or not va:
            return 255.0
        total = sum(abs(pa[i] - pb[i]) for pa, pb in zip(va, vb) for i in range(3))
        return total / (len(va) * 3)
    except (ValueError, TypeError):
        return 255.0


def photo_quality(im) -> float:
    """照片质量分 0-100：边缘能量（清晰度代理）+ 曝光中性度。经验标定，用于排序/自动选片。"""
    try:
        g = im.convert("L").resize((256, 256))
        edges = g.filter(ImageFilter.FIND_EDGES)
        hist = edges.histogram()
        n = sum(hist) or 1
        mean = sum(i * c for i, c in enumerate(hist)) / n
        var = sum(((i - mean) ** 2) * c for i, c in enumerate(hist)) / n
        sharp = min(100.0, var / 20.0)
        lhist = g.histogram()
        lmean = sum(i * c for i, c in enumerate(lhist)) / (256 * 256)
        expo = max(0.0, 100.0 - abs(lmean - 118.0) * 1.2)
        return round(0.65 * sharp + 0.35 * expo, 1)
    except Exception:
        return 0.0


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
        self.sprite_q = queue.Queue()
        self.job_q = queue.Queue()
        self.sprite_dir = self.data_dir / "sprites"
        self.export_dir = self.data_dir / "exports"
        for d in (self.sprite_dir, self.export_dir):
            d.mkdir(parents=True, exist_ok=True)
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
        _ensure_columns(conn, "items", ITEM_MIGRATIONS)
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
                        dirnames[:] = [d for d in sorted(dirnames) if not d.startswith(".")]  # 跳过隐藏目录（含 .djiwb-trash）
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
                            if not re.match(r"(\d{4}-\d{2}-\d{2}|\d{8})", day):
                                day = ""
                            row = conn.execute("SELECT id, size, stable, scans_stable, last_size FROM items WHERE path=?",
                                               (p,)).fetchone()
                            tk = taken_at_str(day, st.st_mtime, p)
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
                                # abs：相机时钟调到未来时 mtime>now，不能让文件永远卡在"上传中"
                                if scans >= int(self.cfg["stable_scans"]) and \
                                        abs(now() - st.st_mtime) > float(self.cfg["stable_age"]) and \
                                        looks_complete(p, ext):
                                    stable = 1
                                    stats["stable"] += 1
                                conn.execute("UPDATE items SET size=?, mtime=?, taken_at=?, day_folder=?, scans_stable=?, stable=?, "
                                             "last_size=?, missing=0, updated_at=? WHERE id=?",
                                             (st.st_size, st.st_mtime, tk, day, scans, stable, st.st_size, now(), row["id"]))
                            else:
                                conn.execute("UPDATE items SET size=?, mtime=?, taken_at=?, day_folder=?, missing=0, updated_at=? WHERE id=?",
                                             (st.st_size, st.st_mtime, tk, day, now(), row["id"]))
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
        row = conn.execute("SELECT sha256, thumb_status, proxy_status, sprite_status FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            return
        if not row["sha256"]:
            self.hash_q.put(item_id)
        if row["thumb_status"] == "none":
            self.thumb_q.put(item_id)
        if kind == "video" and self.cfg.get("proxy_enabled") and row["proxy_status"] == "none":
            self.proxy_q.put(item_id)
        if kind == "video" and row["sprite_status"] == "none":
            self.sprite_q.put(item_id)

    def trigger_scan(self):
        self.wake.set()

    def backfill_exif(self):
        """v0.3 前入库条目补拍摄参数（照片开原图读 EXIF；视频 ffprobe）。"""
        conn = self.db()
        try:
            rows = conn.execute("SELECT id, path, kind FROM items WHERE exif='' AND "
                                "kind IN ('photo','pano','video') AND missing=0").fetchall()
            for r in rows:
                if not os.path.isfile(r["path"]):
                    continue
                try:
                    if r["kind"] == "video":
                        if not self.has_ffprobe:
                            continue
                        params = probe_video(r["path"])[6]
                    else:
                        with Image.open(r["path"]) as im:
                            params = probe_exif_params(im)
                    if params:
                        conn.execute("UPDATE items SET exif=? WHERE id=?",
                                     (json.dumps(params, ensure_ascii=False), r["id"]))
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    def backfill_phash(self):
        """给 v0.2 前入库、已有缩略图的条目补算感知哈希。"""
        conn = self.db()
        try:
            rows = conn.execute("SELECT id FROM items WHERE (phash='' OR chash='') AND "
                                "thumb_status='ready' AND kind IN ('photo','pano','raw','video')").fetchall()
            for r in rows:
                tp = self.thumb_dir / f"{r['id']}.jpg"
                if not tp.exists():
                    continue
                try:
                    with Image.open(tp) as im:
                        conn.execute("UPDATE items SET phash=?, chash=? WHERE id=?",
                                     (dhash(im), chash(im), r["id"]))
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    def start_workers(self):
        try:
            self.backfill_phash()
        except Exception:
            log.exception("phash backfill failed")
        try:
            self.backfill_exif()
        except Exception:
            log.exception("exif backfill failed")
        for target in (self._hash_loop, self._thumb_loop, self._proxy_loop,
                       self._sprite_loop, self._job_loop, self._scanner_loop):
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
            r = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if not r or r["sha256"] or not os.path.isfile(r["path"]):
                return
            digest = sha256_file(r["path"])
            dup = conn.execute("SELECT id FROM items WHERE sha256=? AND id!=? LIMIT 1",
                               (digest, item_id)).fetchone()
            conn.execute("UPDATE items SET sha256=?, duplicate_of=?, updated_at=? WHERE id=?",
                         (digest, dup["id"] if dup else None, now(), item_id))
            conn.commit()
            # 素材安全冗余：稳定入库且非重复时，复制一份到备份目录
            backup = (self.cfg.get("backup_dir") or "").strip()
            if backup and not dup and os.path.isfile(r["path"]):
                try:
                    sub = [p for p in (r["device"], r["day_folder"]) if p]
                    dst_dir = os.path.join(backup, *sub) if sub else backup
                    os.makedirs(dst_dir, exist_ok=True)
                    dst = os.path.join(dst_dir, os.path.basename(r["path"]))
                    if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(r["path"]):
                        shutil.copy2(r["path"], dst + ".tmp")
                        os.replace(dst + ".tmp", dst)
                except OSError as e:
                    log.warning("备份复制失败 id=%s: %s", item_id, e)
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
                    w, h, lat, lon, exif_params = probe_image(r["path"])
                    kind = "pano" if (w and h and w / max(h, 1) >= PANO_RATIO) else "photo"
                    im = self._thumb_from_image(r["path"], self.cfg["thumb_size"])
                    quality = photo_quality(im) if kind == "photo" else 0.0
                    conn.execute("UPDATE items SET width=?, height=?, gps_lat=?, gps_lon=?, kind=?, quality=?, exif=? WHERE id=?",
                                 (w, h, lat, lon, kind, quality,
                                  json.dumps(exif_params, ensure_ascii=False) if exif_params else "", item_id))
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
                        w, h, dur, codec, lat, lon, exif_params = probe_video(r["path"])
                        conn.execute("UPDATE items SET width=?, height=?, duration=?, codec=?, gps_lat=?, gps_lon=?, exif=? "
                                     "WHERE id=?", (w, h, dur, codec, lat, lon,
                                                    json.dumps(exif_params, ensure_ascii=False) if exif_params else "",
                                                    item_id))
                else:
                    self._set_thumb(conn, item_id, "ready", "非媒体文件，仅索引")
                    return
                dest = self.thumb_dir / f"{item_id}.jpg"
                tmp = str(dest) + ".tmp"
                im.save(tmp, "JPEG", quality=85)
                os.replace(tmp, dest)
                conn.execute("UPDATE items SET phash=?, chash=? WHERE id=?",
                             (dhash(im), chash(im), item_id))
                conn.commit()
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
            cmd += ["-i", path, "-frames:v", "1", "-update", "1",
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

    def _prepare_lut(self, lut_path: str) -> str:
        """DJI 官方 LUT 带 LUT_3D_INPUT_RANGE 行，ffmpeg lut3d 解析到该行会整体初始化失败
        （实测 2026-10，Phantom4/Pocket 系官方 cube 都带）。剥掉该行输出净化副本，按 mtime 缓存。
        INPUT_RANGE 0..1 为恒等语义，剥离不改变色彩。"""
        dst_dir = self.data_dir / "luts_clean"
        dst_dir.mkdir(parents=True, exist_ok=True)
        src = Path(lut_path)
        dst = dst_dir / f"{src.stem}-{int(src.stat().st_mtime)}.cube"
        if dst.exists():
            return str(dst)
        raw = src.read_bytes()
        lines = [ln for ln in raw.split(b"\n")
                 if not ln.lstrip().lower().startswith(b"lut_3d_input_range")]
        tmp = str(dst) + ".tmp"
        Path(tmp).write_bytes(b"\n".join(lines))
        os.replace(tmp, dst)
        return str(dst)

    def _transcode(self, src, dst):
        vf = []
        lut = self.cfg.get("lut_path") or ""
        if lut and os.path.isfile(lut):
            esc = self._prepare_lut(lut).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
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
    # ---- 视频雪碧图（悬停刮擦预览）
    def _sprite_loop(self):
        while True:
            item_id = self.sprite_q.get()
            try:
                self.sprite_one(item_id)
            except Exception:
                log.exception("sprite failed id=%s", item_id)
            finally:
                self.sprite_q.task_done()

    def sprite_one(self, item_id):
        conn = self.db()
        try:
            r = conn.execute("SELECT path, duration, sprite_status FROM items WHERE id=?",
                             (item_id,)).fetchone()
            if not r or r["sprite_status"] in ("ready", "generating"):
                return
            if not self.has_ffmpeg or not r["duration"] or not os.path.isfile(r["path"]):
                conn.execute("UPDATE items SET sprite_status='failed', updated_at=? WHERE id=?",
                             (now(), item_id))
                conn.commit()
                return
            conn.execute("UPDATE items SET sprite_status='generating', updated_at=? WHERE id=?",
                         (now(), item_id))
            conn.commit()
            try:
                dur = float(r["duration"])
                interval = max(0.5, dur / SPRITE_FRAMES)
                out = self.sprite_dir / f"{item_id}.jpg"
                tmp = str(out).replace(".jpg", ".tmp.jpg")  # ffmpeg 按扩展名识别封装，必须保持 .jpg
                vf = (f"fps=1/{interval},scale=160:-2:flags=bicubic,tile={SPRITE_FRAMES}x1")
                subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                                "-i", r["path"], "-vf", vf, "-frames:v", "1",
                                "-update", "1", tmp],
                               capture_output=True, timeout=600)
                if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
                    raise RuntimeError("雪碧图生成失败")
                os.replace(tmp, out)
                conn.execute("UPDATE items SET sprite_status='ready', updated_at=? WHERE id=?",
                             (now(), item_id))
            except Exception:
                log.exception("sprite gen failed id=%s", item_id)
                conn.execute("UPDATE items SET sprite_status='failed', updated_at=? WHERE id=?",
                             (now(), item_id))
            conn.commit()
        finally:
            conn.close()

    # ---- 通用任务（导出/幻灯片/AI 打标）
    def _job_loop(self):
        while True:
            job_id = self.job_q.get()
            try:
                self.run_job(job_id)
            except Exception as e:
                log.exception("job failed id=%s", job_id)
                self._job_status(job_id, "failed", note=str(e)[:200])
            finally:
                self.job_q.task_done()

    def create_job(self, kind, payload):
        conn = self.db()
        try:
            cur = conn.execute(
                "INSERT INTO jobs(kind,payload,status,created_at,updated_at) VALUES(?,?,'queued',?,?)",
                (kind, json.dumps(payload, ensure_ascii=False), now(), now()))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _job_status(self, job_id, status, note="", result=None):
        conn = self.db()
        try:
            conn.execute("UPDATE jobs SET status=?, note=?, result=?, updated_at=? WHERE id=?",
                         (status, note,
                          json.dumps(result, ensure_ascii=False) if result is not None else "",
                          now(), job_id))
            conn.commit()
        finally:
            conn.close()

    def run_job(self, job_id):
        conn = self.db()
        try:
            j = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        if not j or j["status"] != "queued":
            return
        payload = json.loads(j["payload"] or "{}")
        self._job_status(job_id, "running")
        try:
            if j["kind"] == "export":
                result = self.run_export_job(payload)
            elif j["kind"] == "slideshow":
                result = self.run_slideshow_job(payload)
            elif j["kind"] == "ai_tag":
                result = self.run_ai_tag_job(payload)
            else:
                raise ValueError("unknown job kind: " + str(j["kind"]))
        except Exception as e:
            self._job_status(job_id, "failed", note=str(e)[:300])
            return
        self._job_status(job_id, "done", result=result)

    # ---- 导出（照片调色/水印/缩放，打包 zip）
    def _font_path(self):
        path = (self.cfg.get("export_font_path") or "").strip()
        if path and os.path.isfile(path):
            return path
        for p in _FONT_CANDIDATES:
            if os.path.isfile(p):
                return p
        return None

    def _find_font(self, size):
        from PIL import ImageFont
        path = self._font_path()
        if path:
            return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    def _video_export_filters(self, opts):
        """导出用视频滤镜链，语义与照片 PIL 调色对齐（log LUT→色温→eq→暗角→缩放→水印）。"""
        vf = []
        lut = (self.cfg.get("lut_path") or "").strip()
        if lut and os.path.isfile(lut):
            esc = self._prepare_lut(lut).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            vf.append(f"lut3d='{esc}'")
        t = int(opts.get("temp") or 0)
        if t:
            vf.append(f"colortemperature=temperature={6500 + max(-50, min(50, t)) * 40}")
        b = float(opts.get("brightness") or 0)
        c = float(opts.get("contrast") or 0)
        sa = float(opts.get("saturation") or 0)
        if b or c or sa:
            vf.append(f"eq=brightness={b / 150:.3f}:contrast={1 + c / 100:.3f}:saturation={1 + sa / 50:.3f}")
        st = int(opts.get("vignette") or 0)
        if st:
            angle = 3.14159265 * (0.125 + max(0, min(100, st)) / 100 * 0.25)
            vf.append(f"vignette=angle={angle:.4f}")
        rw = int(opts.get("resize_w") or 0)
        if rw:
            vf.append(f"scale={rw}:-2:flags=bicubic")
        wm = str(opts.get("watermark_text") or "").strip()
        font = self._font_path()
        if wm and font and self._has_drawtext():
            txt = (wm.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")
                   .replace("%", "\\%"))
            fesc = font.replace("\\", "\\\\").replace(":", "\\:")
            fs = "main_h/26"
            pos = opts.get("watermark_pos") or "br"
            xy = {"br": ("main_w-tw-24", "main_h-th-24"),
                  "bl": ("24", "main_h-th-24"),
                  "tr": ("main_w-tw-24", "24"),
                  "tl": ("24", "24")}.get(pos, ("main_w-tw-24", "main_h-th-24"))
            vf.append(f"drawtext=fontfile='{fesc}':text='{txt}':fontsize={fs}:"
                      f"fontcolor=white@0.92:shadowcolor=black@0.65:shadowx=2:shadowy=2:"
                      f"x={xy[0]}:y={xy[1]}")
        elif wm:
            reason = "ffmpeg 无 drawtext 滤镜（缺 libfreetype）" if not self._has_drawtext() else "找不到可用字体"
            log.warning("视频水印跳过（%s）：%s", reason, wm)
        return vf

    def _has_drawtext(self):
        if not hasattr(self, "_drawtext_ok"):
            try:
                out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                     capture_output=True, text=True, timeout=30)
                self._drawtext_ok = " drawtext " in out.stdout
            except Exception:
                self._drawtext_ok = False
        return self._drawtext_ok

    def _export_video(self, src, dst, opts):
        vf = self._video_export_filters(opts)
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        cmd += ["-c:v", "libx264", "-preset", "veryfast",
                "-crf", str(opts.get("video_crf") or 22), "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)]
        subprocess.run(cmd, capture_output=True, timeout=3600)
        if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            raise RuntimeError("视频导出转码失败")

    def apply_photo_adjustments(self, im, opts):
        from PIL import ImageEnhance
        if opts.get("temp"):
            t = max(-50, min(50, int(opts["temp"])))
            if t:
                # 暖冷色温：R/B 通道乘法调整（混灰叠加会洗掉真黑，用乘法）
                f = t / 50.0 * 0.25
                rr, gg, bb = im.convert("RGB").split()
                rr = rr.point(lambda v: min(255, int(v * (1 + f))))
                bb = bb.point(lambda v: int(v * (1 - f)))
                im = Image.merge("RGB", (rr, gg, bb))
        if opts.get("brightness"):
            im = ImageEnhance.Brightness(im).enhance(1 + float(opts["brightness"]) / 100)
        if opts.get("contrast"):
            im = ImageEnhance.Contrast(im).enhance(1 + float(opts["contrast"]) / 100)
        if opts.get("saturation"):
            im = ImageEnhance.Color(im).enhance(1 + float(opts["saturation"]) / 100)
        if opts.get("vignette"):
            strength = max(0, min(100, int(opts["vignette"]))) / 100.0
            w, h = im.size
            # 椭圆收紧到 0.05：四角距椭圆边界足够远，blur 后仍保持低 mask（四角暗、中心亮）
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).ellipse([-w * 0.05, -h * 0.05, w * 1.05, h * 1.05], fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(max(8, min(w, h) // 8)))
            dark = Image.blend(im, Image.new("RGB", (w, h), (0, 0, 0)), 0.75 * strength)
            im = Image.composite(im, dark, mask)
        if opts.get("watermark_text"):
            text = str(opts["watermark_text"])[:60]
            size = max(16, int(min(im.size) * 0.03))
            font = self._find_font(size)
            d = ImageDraw.Draw(im)
            try:
                box = d.textbbox((0, 0), text, font=font)
                tw, th = box[2] - box[0], box[3] - box[1]
            except Exception:
                tw, th = len(text) * size, size
            pad = int(min(im.size) * 0.03)
            pos = str(opts.get("watermark_pos") or "br")
            xy = {"br": (im.width - tw - pad, im.height - th - pad),
                  "bl": (pad, im.height - th - pad),
                  "tr": (im.width - tw - pad, pad),
                  "tl": (pad, pad)}.get(pos, (im.width - tw - pad, im.height - th - pad))
            d.text((xy[0] + 2, xy[1] + 2), text, font=font, fill=(0, 0, 0))   # 阴影
            d.text(xy, text, font=font, fill=(255, 255, 255))
        if opts.get("resize_w"):
            rw = int(opts["resize_w"])
            if 0 < rw < im.width:
                im = im.resize((rw, max(1, int(im.height * rw / im.width))), Image.LANCZOS)
        return im

    def run_export_job(self, payload):
        import zipfile
        ids = payload.get("ids") or []
        opts = payload.get("opts") or {}
        out_dir = self.export_dir / f"export_{int(now() * 1000)}"
        out_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        conn = self.db()
        try:
            rows = [conn.execute("SELECT * FROM items WHERE id=?", (i,)).fetchone() for i in ids]
        finally:
            conn.close()
        include_video = bool(opts.get("include_video"))
        for r in rows:
            if not r or not os.path.isfile(r["path"]):
                continue
            name = os.path.splitext(os.path.basename(r["path"]))[0]
            if r["kind"] == "video":
                if include_video:
                    self._export_video(r["path"], out_dir / f"{name}.mp4", opts)
                    count += 1
                continue
            if r["kind"] not in ("photo", "pano", "raw"):
                continue
            try:
                if r["kind"] == "raw":
                    blob = extract_dng_preview(r["path"])
                    im = Image.open(io.BytesIO(blob)).convert("RGB") if blob else None
                else:
                    im = ImageOps.exif_transpose(Image.open(r["path"])).convert("RGB")
            except Exception:
                continue
            if im is None:
                continue
            im = self.apply_photo_adjustments(im, opts)
            fmt = "png" if str(opts.get("format")).lower() == "png" else "jpg"
            im.save(out_dir / f"{name}.{fmt}", quality=92)
            count += 1
        if count == 0:
            raise RuntimeError("没有可导出的素材（勾选视频需在导出选项里开启）")
        zip_path = str(out_dir) + ".zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
            for f in sorted(out_dir.iterdir()):
                z.write(f, f.name)
        shutil.rmtree(out_dir, ignore_errors=True)
        return {"zip": zip_path, "count": count}

    # ---- 自动小片（照片幻灯片 kenburns）
    def run_slideshow_job(self, payload):
        ids = payload.get("ids") or []
        per = max(1.5, min(8.0, float(payload.get("duration_per") or 3)))
        music = (payload.get("music_path") or "").strip()
        conn = self.db()
        try:
            paths = []
            for i in ids:
                r = conn.execute("SELECT path, kind FROM items WHERE id=? AND missing=0",
                                 (i,)).fetchone()
                if r and r["kind"] in ("photo", "pano") and os.path.isfile(r["path"]):
                    paths.append(r["path"])
        finally:
            conn.close()
        if not paths:
            raise RuntimeError("没有可用照片")
        work = self.export_dir / f"slide_{int(now() * 1000)}"
        work.mkdir(parents=True, exist_ok=True)
        W, H, FPS = 1920, 1080, 24
        n_frames = int(per * FPS)
        segs = []
        for idx, p in enumerate(paths):
            seg = work / f"s{idx:03d}.mp4"
            if idx % 2 == 0:  # 交替推近/拉远
                zexpr = f"min(1+0.0012*on,1.18)"
            else:
                zexpr = f"max(1.18-0.0012*on,1.0)"
            vf = (f"scale=2400:-2:flags=lanczos,crop=2400:{int(2400 * 9 / 16)},"
                  f"zoompan=z='{zexpr}':d={n_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                  f"s={W}x{H}:fps={FPS},format=yuv420p")
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-i", p, "-vf", vf, "-frames:v", str(n_frames),
                            "-c:v", "libx264", "-preset", "veryfast", str(seg)],
                           capture_output=True, timeout=1800)
            if not seg.exists() or seg.stat().st_size == 0:
                raise RuntimeError(f"片段渲染失败: {os.path.basename(p)}")
            segs.append(seg)
        lst = work / "list.txt"
        lst.write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")
        out = self.export_dir / f"slideshow_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-f", "concat", "-safe", "0", "-i", str(lst)]
        if music and os.path.isfile(music):
            cmd += ["-stream_loop", "-1", "-i", music, "-shortest",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k"]
        else:
            cmd += ["-c", "copy"]
        cmd += ["-movflags", "+faststart", str(out)]
        subprocess.run(cmd, capture_output=True, timeout=1800)
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError("成片封装失败")
        shutil.rmtree(work, ignore_errors=True)
        return {"mp4": str(out), "count": len(paths), "seconds": round(len(paths) * per, 1)}

    # ---- AI 打标（OpenAI 兼容多模态接口）
    def _ai_call(self, thumb_path):
        import base64
        import urllib.request
        with open(thumb_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        url = self.cfg["ai_base_url"].rstrip("/") + "/chat/completions"
        body = json.dumps({
            "model": self.cfg["ai_model"],
            "messages": [{"role": "user", "content": [
                {"type": "text",
                 "text": "给这张照片输出3-6个中文标签，用逗号分隔，不要解释。涵盖场景/主体/光线/氛围。"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ]}],
            "max_tokens": 200,
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self.cfg["ai_api_key"]})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()  # 推理模型剥思考块
        return [t.strip() for t in re.split(r"[,，、;；\s]+", content) if t.strip()][:8]

    def run_ai_tag_job(self, payload):
        ids = payload.get("ids") or []
        if not (self.cfg.get("ai_base_url") and self.cfg.get("ai_api_key") and self.cfg.get("ai_model")):
            raise RuntimeError("未配置 AI 接口（设置页填写 base_url / key / model 后重试）")
        done = failed = 0
        notes = []
        for item_id in ids:
            conn = self.db()
            try:
                r = conn.execute("SELECT kind, thumb_status FROM items WHERE id=?",
                                 (item_id,)).fetchone()
            finally:
                conn.close()
            if not r or r["kind"] not in ("photo", "pano"):
                continue
            thumb = self.thumb_dir / f"{item_id}.jpg"
            if not thumb.exists():
                failed += 1
                notes.append(f"#{item_id} 缩略图未生成")
                continue
            try:
                tags = self._ai_call(str(thumb))
                conn = self.db()
                try:
                    conn.execute("UPDATE items SET tags=?, updated_at=? WHERE id=?",
                                 (json.dumps(tags, ensure_ascii=False), now(), item_id))
                    conn.commit()
                finally:
                    conn.close()
                done += 1
            except Exception as e:
                failed += 1
                notes.append(f"#{item_id} {str(e)[:80]}")
        return {"done": done, "failed": failed, "notes": notes[:10]}

    # ---- 自动选片（同目录连拍分组，质量分最高者为建议保留）
    def auto_pick_groups(self, day):
        conn = self.db()
        try:
            rows = conn.execute(
                "SELECT * FROM items WHERE missing=0 AND kind='photo' AND rejected=0 "
                "AND duplicate_of IS NULL AND substr(taken_at,1,10)=? ORDER BY path, mtime",
                (day,)).fetchall()
        finally:
            conn.close()
        by_dir = {}
        for r in rows:
            by_dir.setdefault(os.path.dirname(r["path"]), []).append(dict(r))
        groups = []
        for items in by_dir.values():
            items.sort(key=lambda x: x["mtime"])
            cur = [items[0]]
            for it in items[1:]:
                if it["mtime"] - cur[-1]["mtime"] <= 2.0:
                    cur.append(it)
                else:
                    groups.append(cur)
                    cur = [it]
            groups.append(cur)
        out = []
        for g in groups:
            if len(g) < 2:
                continue
            best, best_q = None, -1.0
            for it in g:
                q = float(it["quality"] or 0)
                if q <= 0:
                    try:
                        q = photo_quality(self._thumb_from_image(it["path"], 256))
                    except Exception:
                        q = 0.0
                    conn = self.db()
                    try:
                        conn.execute("UPDATE items SET quality=? WHERE id=?", (q, it["id"]))
                        conn.commit()
                    finally:
                        conn.close()
                    it["quality"] = q
                if q > best_q:
                    best, best_q = it, q
            out.append({"suggest_id": best["id"], "quality": best_q, "ids": [x["id"] for x in g]})
        return out

    def queue_depths(self):
        return {"hash": self.hash_q.qsize(), "thumb": self.thumb_q.qsize(),
                "proxy": self.proxy_q.qsize(), "sprite": self.sprite_q.qsize(),
                "job": self.job_q.qsize()}


# ---------------------------------------------------------------- Flask

def create_app(wb: WB):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    def item_row(r):
        d = {c: r[c] for c in ITEM_COLS if c in r.keys()}
        d["thumb_url"] = f"/api/items/{r['id']}/thumb" if r["thumb_status"] == "ready" and r["kind"] not in ("sidecar", "audio") else ""
        d["proxy_url"] = f"/api/items/{r['id']}/proxy" if r["proxy_status"] == "ready" else ""
        d["sprite_url"] = f"/api/items/{r['id']}/sprite" if r["sprite_status"] == "ready" else ""
        d["pano"] = 1 if (r["kind"] == "pano" or (r["width"] and r["height"] and r["width"] / max(r["height"], 1) >= PANO_RATIO)) else 0
        try:
            d["tags"] = json.loads(r["tags"]) if r["tags"] else []
        except (TypeError, ValueError):
            d["tags"] = []
        try:
            d["exif"] = json.loads(r["exif"]) if r["exif"] else {}
        except (TypeError, ValueError):
            d["exif"] = {}
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
            if kind in ("photo", "video", "raw", "pano", "sidecar", "audio"):
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
            album = q.get("album")
            if album:
                where.append("id IN (SELECT item_id FROM album_items WHERE album_id=?)")
                args.append(int(album))
            min_quality = q.get("min_quality")
            if min_quality:
                where.append("kind IN ('photo','pano') AND quality>=?")
                args.append(float(min_quality))
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
                where.append("(rel_path LIKE ? OR device LIKE ? OR tags LIKE ?)")
                args += [f"%{text}%", f"%{text}%", f"%{text}%"]
            sort = q.get("sort", "date_desc")
            order = {"date_desc": "taken_at DESC, id DESC", "date_asc": "taken_at ASC, id ASC",
                     "size_desc": "size DESC", "rating_desc": "rating DESC, taken_at DESC",
                     "quality_desc": "quality DESC, taken_at DESC"}.get(sort, "taken_at DESC, id DESC")
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

    @app.route("/api/items/batch", methods=["POST"])
    def batch_ops():
        body = request.get_json(silent=True) or {}
        ids = body.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return jsonify({"error": "ids 为空"}), 400
        ids = [int(i) for i in ids if str(i).isdigit()][:2000]
        conn = wb.db()
        try:
            n = 0
            if "rating" in body or "rejected" in body:
                sets, args = [], []
                if "rating" in body:
                    sets.append("rating=?")
                    args.append(max(0, min(5, int(body["rating"] or 0))))
                if "rejected" in body:
                    sets.append("rejected=?")
                    args.append(1 if body["rejected"] else 0)
                if sets:
                    marks = ",".join("?" * len(ids))
                    args += [now()] + ids
                    n = conn.execute(f"UPDATE items SET {', '.join(sets)}, updated_at=? "
                                     f"WHERE id IN ({marks})", args).rowcount
            album_add = body.get("album_add")
            if album_add:
                for i in ids:
                    conn.execute("INSERT OR IGNORE INTO album_items(album_id,item_id,added_at) "
                                 "VALUES(?,?,?)", (int(album_add), i, now()))
                n = max(n, len(ids))
            album_remove = body.get("album_remove")
            if album_remove:
                marks = ",".join("?" * len(ids))
                n = max(n, conn.execute(f"DELETE FROM album_items WHERE album_id=? AND "
                                        f"item_id IN ({marks})", [int(album_remove)] + ids).rowcount)
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "updated": n})

    # ---- 相册
    @app.route("/api/albums", methods=["GET"])
    def list_albums():
        conn = wb.db()
        try:
            rows = conn.execute(
                "SELECT a.id, a.name, a.created_at, COUNT(ai.item_id) count "
                "FROM albums a LEFT JOIN album_items ai ON ai.album_id=a.id "
                "GROUP BY a.id ORDER BY a.created_at DESC").fetchall()
        finally:
            conn.close()
        return jsonify({"albums": [dict(r) for r in rows]})

    @app.route("/api/albums", methods=["POST"])
    def create_album():
        name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
        if not name:
            return jsonify({"error": "名称不能为空"}), 400
        conn = wb.db()
        try:
            try:
                cur = conn.execute("INSERT INTO albums(name, created_at) VALUES(?,?)",
                                   (name, now()))
                conn.commit()
                return jsonify({"ok": True, "id": cur.lastrowid})
            except sqlite3.IntegrityError:
                return jsonify({"error": "同名相册已存在"}), 400
        finally:
            conn.close()

    @app.route("/api/albums/<int:album_id>", methods=["DELETE"])
    def delete_album(album_id):
        conn = wb.db()
        try:
            conn.execute("DELETE FROM album_items WHERE album_id=?", (album_id,))
            conn.execute("DELETE FROM albums WHERE id=?", (album_id,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    # ---- 地图（有 GPS 坐标的素材）
    @app.route("/api/map")
    def map_items():
        conn = wb.db()
        try:
            rows = conn.execute(
                "SELECT id, kind, gps_lat lat, gps_lon lon, taken_at, rel_path, device, "
                "thumb_status FROM items WHERE missing=0 AND gps_lat IS NOT NULL "
                "AND duplicate_of IS NULL ORDER BY taken_at DESC LIMIT 5000").fetchall()
        finally:
            conn.close()
        return jsonify({"points": [dict(r) for r in rows]})

    # ---- 自动选片（连拍分组）
    @app.route("/api/autopick")
    def autopick():
        date = request.args.get("date", "")
        if not re.match(r"\d{4}-\d{2}-\d{2}", date):
            return jsonify({"error": "需要 date=YYYY-MM-DD"}), 400
        return jsonify({"date": date, "groups": wb.auto_pick_groups(date)})

    # ---- 相似素材（感知哈希）
    @app.route("/api/items/<int:item_id>/similar")
    def similar_items(item_id):
        try:
            threshold = max(1, min(40, int(request.args.get("threshold", 10))))
        except ValueError:
            threshold = 10
        conn = wb.db()
        try:
            base = conn.execute("SELECT phash, chash FROM items WHERE id=?", (item_id,)).fetchone()
            if not base or not base["phash"]:
                return jsonify({"base": item_id, "threshold": threshold, "items": [],
                                "note": "该条目还没有感知哈希（缩略图未生成或类型不支持）"})
            base_chash = base["chash"] if "chash" in base.keys() else ""
            rows = conn.execute(
                "SELECT id, kind, phash, chash, taken_at, rel_path, device, rating, rejected, "
                "thumb_status, duplicate_of FROM items "
                "WHERE phash!='' AND id!=? AND missing=0", (item_id,)).fetchall()
        finally:
            conn.close()
        scored = []
        for r in rows:
            d = phash_distance(base["phash"], r["phash"])
            if d > threshold:
                continue
            # 颜色门控：结构像但颜色差太远（如同构图昼夜两张）不算相似
            if base_chash and r["chash"]:
                cd = chash_distance(base_chash, r["chash"])
                if cd > 35.0:   # 实测标定：连拍 <10、同场景 10-25、跨场景 40+（演示库校准 2026-10-26）
                    continue
            else:
                cd = None
            d2 = {c: r[c] for c in ("id", "kind", "taken_at", "rel_path", "device",
                                    "rating", "rejected", "duplicate_of")}
            d2["distance"] = d
            d2["color_distance"] = round(cd, 1) if cd is not None else None
            d2["thumb_url"] = f"/api/items/{r['id']}/thumb" if r["thumb_status"] == "ready" else ""
            scored.append(d2)
        scored.sort(key=lambda x: x["distance"])
        return jsonify({"base": item_id, "threshold": threshold,
                        "items": scored[:60]})

    # ---- 重复管理
    @app.route("/api/duplicates")
    def dup_groups():
        conn = wb.db()
        try:
            rows = conn.execute(
                "SELECT id, rel_path, device, kind, size, taken_at, rating, rejected, "
                "thumb_status, sha256, duplicate_of FROM items "
                "WHERE sha256!='' AND missing=0 ORDER BY id").fetchall()
        finally:
            conn.close()
        by_sha = {}
        for r in rows:
            by_sha.setdefault(r["sha256"], []).append(r)
        groups = []
        for sha, items in by_sha.items():
            if len(items) < 2:
                continue
            members = []
            for r in items:
                members.append({
                    "id": r["id"], "rel_path": r["rel_path"], "device": r["device"],
                    "kind": r["kind"], "size": r["size"], "taken_at": r["taken_at"],
                    "rating": r["rating"], "rejected": r["rejected"],
                    "thumb_url": f"/api/items/{r['id']}/thumb" if r["thumb_status"] == "ready" else "",
                })
            groups.append({"sha256": sha, "wasted": sum(m["size"] for m in members[1:]),
                           "keep_id": members[0]["id"], "items": members})
        groups.sort(key=lambda g: -g["wasted"])
        return jsonify({"groups": groups,
                        "total_wasted": sum(g["wasted"] for g in groups)})

    @app.route("/api/items/trash", methods=["POST"])
    def trash_items():
        """把文件移入同目录 .djiwb-trash/（同卷 rename 原子、不跨盘，扫描自动跳过；可人工捞回）。"""
        body = request.get_json(silent=True) or {}
        ids = [int(i) for i in (body.get("ids") or []) if str(i).isdigit()][:2000]
        if not ids:
            return jsonify({"error": "ids 为空"}), 400
        moved, errors = 0, []
        conn = wb.db()
        try:
            for item_id in ids:
                r = conn.execute("SELECT path, missing FROM items WHERE id=?", (item_id,)).fetchone()
                if not r or r["missing"]:
                    continue
                src = r["path"]
                if not os.path.isfile(src):
                    continue
                tdir = os.path.join(os.path.dirname(src), ".djiwb-trash")
                try:
                    os.makedirs(tdir, exist_ok=True)
                    dst = os.path.join(tdir, os.path.basename(src))
                    if os.path.exists(dst):
                        dst = os.path.join(tdir, f"{int(now() * 1000)}_{os.path.basename(src)}")
                    os.rename(src, dst)
                    conn.execute("UPDATE items SET missing=1, updated_at=? WHERE id=?",
                                 (now(), item_id))
                    moved += 1
                except OSError as e:
                    errors.append(f"#{item_id} {str(e)[:80]}")
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "moved": moved, "errors": errors})

    # ---- 存储统计与缓存清理
    @app.route("/api/storage")
    def storage_stats():
        def dir_size(path):
            total = 0
            if os.path.isdir(path):
                for dp, _, fns in os.walk(path):
                    for fn in fns:
                        try:
                            total += os.path.getsize(os.path.join(dp, fn))
                        except OSError:
                            pass
            return total
        conn = wb.db()
        try:
            by_device = [{"name": r["device"] or "(未分设备)", "count": r["c"], "size": r["s"] or 0}
                         for r in conn.execute(
                             "SELECT device, COUNT(*) c, SUM(size) s FROM items "
                             "WHERE missing=0 AND kind!='sidecar' GROUP BY device ORDER BY s DESC")]
            by_day = [{"day": r["d"], "count": r["c"], "size": r["s"] or 0}
                      for r in conn.execute(
                          "SELECT substr(taken_at,1,10) d, COUNT(*) c, SUM(size) s FROM items "
                          "WHERE missing=0 AND taken_at!='' GROUP BY d ORDER BY s DESC LIMIT 60")]
            total_size = conn.execute("SELECT COALESCE(SUM(size),0) s FROM items WHERE missing=0").fetchone()["s"]
            trashed = conn.execute("SELECT COUNT(*) c FROM items WHERE missing=1").fetchone()["c"]
        finally:
            conn.close()
        cache = {name: dir_size(str(d)) for name, d in
                 (("thumbs", wb.thumb_dir), ("posters", wb.poster_dir),
                  ("proxies", wb.proxy_dir), ("sprites", wb.sprite_dir),
                  ("exports", wb.export_dir))}
        return jsonify({"by_device": by_device, "by_day": by_day,
                        "total_size": total_size, "trashed": trashed, "cache": cache})

    @app.route("/api/storage/cleanup", methods=["POST"])
    def storage_cleanup():
        targets = (request.get_json(silent=True) or {}).get("targets") or []
        allowed = {"posters": wb.poster_dir, "sprites": wb.sprite_dir, "exports": wb.export_dir}
        freed = 0
        cleaned = []
        for t in targets:
            d = allowed.get(t)
            if not d or not os.path.isdir(d):
                continue
            for dp, _, fns in os.walk(d):
                for fn in fns:
                    fp = os.path.join(dp, fn)
                    try:
                        freed += os.path.getsize(fp)
                        os.remove(fp)
                    except OSError:
                        pass
            cleaned.append(t)
        return jsonify({"ok": True, "cleaned": cleaned, "freed": freed})

    # ---- 异步任务（导出/幻灯片/AI 打标）
    @app.route("/api/jobs", methods=["GET"])
    def list_jobs():
        conn = wb.db()
        try:
            rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 30").fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
                d["result"] = json.loads(d["result"]) if d["result"] else None
            except ValueError:
                pass
            out.append(d)
        return jsonify({"jobs": out})

    @app.route("/api/jobs", methods=["POST"])
    def create_job_route():
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        if kind not in ("export", "slideshow", "ai_tag"):
            return jsonify({"error": "未知任务类型"}), 400
        if not isinstance(body.get("ids"), list) or not body["ids"]:
            return jsonify({"error": "ids 为空"}), 400
        job_id = wb.create_job(kind, {"ids": [int(i) for i in body["ids"]],
                                      **{k: v for k, v in body.items() if k not in ("kind", "ids")}})
        wb.job_q.put(job_id)
        return jsonify({"ok": True, "job_id": job_id})

    @app.route("/api/jobs/<int:job_id>")
    def job_status(job_id):
        conn = wb.db()
        try:
            r = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        if not r:
            abort(404)
        d = dict(r)
        try:
            d["result"] = json.loads(d["result"]) if d["result"] else None
        except ValueError:
            pass
        return jsonify(d)

    @app.route("/api/jobs/<int:job_id>/download")
    def job_download(job_id):
        conn = wb.db()
        try:
            r = conn.execute("SELECT result FROM jobs WHERE id=? AND status='done'",
                             (job_id,)).fetchone()
        finally:
            conn.close()
        if not r:
            abort(404)
        try:
            path = (json.loads(r["result"]) or {}).get("zip") or (json.loads(r["result"]) or {}).get("mp4")
        except ValueError:
            path = None
        if not path or not os.path.isfile(path):
            return jsonify({"error": "产物文件不存在（可能已被清理）"}), 404
        real = os.path.realpath(path)
        if not real.startswith(str(wb.export_dir.resolve())):
            abort(403)
        return send_file(real, as_attachment=True,
                         download_name=os.path.basename(real))

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
        elif sub == "sprite":
            path = str(wb.sprite_dir / f"{item_id}.jpg")
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

    @app.route("/api/items/<int:item_id>/sprite")
    def serve_sprite(item_id):
        return _serve_item_file(item_id, "sprite")

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
            elif k == "lut_path" or k == "backup_dir" or k == "export_font_path":
                wb.cfg[k] = str(body[k]).strip()
            elif k in ("ai_base_url", "ai_api_key", "ai_model"):
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
                     st.st_size, st.st_mtime, taken_at_str("", st.st_mtime, p), now(), now()))
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
