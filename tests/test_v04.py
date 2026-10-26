# -*- coding: utf-8 -*-
"""v0.4 测试：EXIF 参数/重复管理/回收/存储统计"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.web_app import DEFAULT_CONFIG, WB, create_app

HAS_FFMPEG = bool(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0)


def make_jpeg(path, size=(640, 480), color=(120, 160, 90)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    return path


class V04Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="djiwb-v04-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.day = os.path.join(self.tmp, "DJI Device", "Cam", "2026-10-26")
        os.makedirs(self.day, exist_ok=True)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(watch_roots=[os.path.join(self.tmp, "DJI Device")],
                   stable_age=0, stable_scans=2, proxy_enabled=False)
        self.wb = WB(cfg, Path(self.tmp) / "data", start_workers=False)
        self.client = create_app(self.wb).test_client()

    def rows(self, sql, args=()):
        conn = self.wb.db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def scan_seed(self):
        for _ in range(4):
            self.wb.scan_once()
        for r in self.rows("SELECT id FROM items"):
            self.wb.hash_one(r["id"])
            self.wb.thumb_one(r["id"])

    # ---- EXIF 参数
    def test_photo_exif_params(self):
        im = Image.new("RGB", (400, 300), (90, 120, 150))
        exif = im.getexif()
        e = exif.get_ifd(0x8769)
        e[0x829D] = (28, 10)      # F4.0? 28/10=2.8
        e[0x829A] = (1, 125)      # 1/125s
        e[0x8827] = 200
        e[0x920A] = (240, 10)     # 24mm
        im.save(os.path.join(self.day, "EX_0001.JPG"), exif=exif)
        make_jpeg(os.path.join(self.day, "NOEX_0002.JPG"))
        self.scan_seed()
        rows = {os.path.basename(r["rel_path"]): r for r in self.rows("SELECT rel_path, exif FROM items")}
        p = json.loads(rows["EX_0001.JPG"]["exif"])
        self.assertAlmostEqual(p["f_number"], 2.8, places=1)
        self.assertAlmostEqual(p["exposure"], 1 / 125, places=5)
        self.assertEqual(p["iso"], 200)
        self.assertAlmostEqual(p["focal"], 24.0, places=1)
        self.assertEqual(json.loads(rows["NOEX_0002.JPG"]["exif"] or "{}"), {})
        # API 层
        item = self.client.get("/api/items").get_json()["items"][0]
        self.assertIn("exif", item)

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_video_exif_params(self):
        p = os.path.join(self.day, "V.MP4")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc2=duration=1:size=320x240:rate=24",
                        "-b:v", "500k", "-pix_fmt", "yuv420p", p], check=True, capture_output=True)
        self.scan_seed()
        row = self.rows("SELECT exif FROM items WHERE kind='video'")[0]
        params = json.loads(row["exif"])
        self.assertAlmostEqual(params["fps"], 24.0, delta=0.5)
        self.assertGreater(params.get("bitrate", 0), 0)

    def test_exif_backfill(self):
        im = Image.new("RGB", (400, 300), (60, 60, 60))
        exif = im.getexif()
        e = exif.get_ifd(0x8769)
        e[0x8827] = 100
        im.save(os.path.join(self.day, "BF_0001.JPG"), exif=exif)
        self.scan_seed()
        conn = self.wb.db()
        conn.execute("UPDATE items SET exif=''")
        conn.commit()
        conn.close()
        self.wb.backfill_exif()
        row = self.rows("SELECT exif FROM items WHERE rel_path LIKE '%BF_0001%'")[0]
        self.assertEqual(json.loads(row["exif"]).get("iso"), 100)

    # ---- 重复分组与回收
    def test_duplicates_and_trash(self):
        src = make_jpeg(os.path.join(self.day, "D_0001.JPG"))
        dst = make_jpeg(os.path.join(self.day, "D_0002.JPG"))
        make_jpeg(os.path.join(self.day, "U_0003.JPG"), color=(1, 2, 3))
        self.scan_seed()
        j = self.client.get("/api/duplicates").get_json()
        self.assertEqual(len(j["groups"]), 1)
        g = j["groups"][0]
        self.assertEqual(len(g["items"]), 2)
        self.assertEqual(g["keep_id"], g["items"][0]["id"])
        self.assertGreater(g["wasted"], 0)
        self.assertGreater(j["total_wasted"], 0)
        # 回收副本
        dup_id = g["items"][1]["id"]
        r = self.client.post("/api/items/trash", json={"ids": [dup_id]}).get_json()
        self.assertEqual(r["moved"], 1)
        # 文件移到 .djiwb-trash
        tdir = os.path.join(self.day, ".djiwb-trash")
        self.assertTrue(os.path.isdir(tdir))
        self.assertEqual(len(os.listdir(tdir)), 1)
        self.assertFalse(os.path.exists(dst))
        self.assertTrue(os.path.exists(src))
        # 条目标记 missing（默认筛选不再显示）
        row = self.rows("SELECT missing FROM items WHERE id=?", (dup_id,))[0]
        self.assertEqual(row["missing"], 1)
        # 隐藏目录不会被扫描重新索引
        self.wb.scan_once()
        trash_rows = self.rows("SELECT COUNT(*) c FROM items WHERE path LIKE '%.djiwb-trash%'")[0]
        self.assertEqual(trash_rows["c"], 0)
        # 库里剩 2 项可见
        self.assertEqual(self.client.get("/api/items").get_json()["total"], 2)
        # 重复组消失
        self.assertEqual(len(self.client.get("/api/duplicates").get_json()["groups"]), 0)
        # 空 ids 400
        self.assertEqual(self.client.post("/api/items/trash", json={}).status_code, 400)

    # ---- 存储统计与清理
    def test_storage_stats_and_cleanup(self):
        make_jpeg(os.path.join(self.day, "S_0001.JPG"))
        self.scan_seed()
        # 造导出缓存
        ids = [r["id"] for r in self.rows("SELECT id FROM items")]
        job = self.wb.create_job("export", {"ids": ids, "opts": {}})
        self.wb.run_job(job)
        j = self.client.get("/api/storage").get_json()
        self.assertGreater(j["total_size"], 0)
        self.assertGreaterEqual(len(j["by_device"]), 1)
        self.assertGreaterEqual(len(j["by_day"]), 1)
        self.assertIn("exports", j["cache"])
        self.assertGreater(j["cache"]["exports"], 0)
        # 清理 exports
        r = self.client.post("/api/storage/cleanup", json={"targets": ["exports"]}).get_json()
        self.assertEqual(r["cleaned"], ["exports"])
        self.assertGreater(r["freed"], 0)
        j2 = self.client.get("/api/storage").get_json()
        self.assertEqual(j2["cache"]["exports"], 0)
        # 非法目标忽略
        r2 = self.client.post("/api/storage/cleanup", json={"targets": ["thumbs", "nope"]}).get_json()
        self.assertEqual(r2["cleaned"], [])


if __name__ == "__main__":
    unittest.main()
