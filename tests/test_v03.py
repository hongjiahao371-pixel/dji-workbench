# -*- coding: utf-8 -*-
"""v0.3 测试：dhash/相似聚类/视频导出/jobs 列表"""
import io
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

from app.web_app import DEFAULT_CONFIG, WB, create_app, dhash, phash_distance

HAS_FFMPEG = bool(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0)


def make_jpeg(path, size=(640, 480), color=(120, 160, 90), mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


class V03Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="djiwb-v03-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.day = os.path.join(self.tmp, "DJI Device", "Cam", "2026-10-25")
        self.sd = os.path.join(self.tmp, "sdcard", "DCIM")   # 跨目录相似场景
        os.makedirs(self.day, exist_ok=True)
        os.makedirs(self.sd, exist_ok=True)
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

    def seed_similar(self):
        """同一画面三份拷贝分居两个目录（拷贝走 SD 导入通道）+ 一张不同画面"""
        im = Image.new("RGB", (640, 480), (10, 90, 180))
        d = ImageDraw.Draw(im)
        d.ellipse([80, 60, 560, 420], fill=(230, 200, 60))
        im.save(os.path.join(self.day, "A_0001.JPG"), "JPEG", quality=92)
        im.save(os.path.join(self.sd, "A_copy.JPG"), "JPEG", quality=92)
        im2 = im.copy()
        d2 = ImageDraw.Draw(im2)
        d2.rectangle([260, 180, 380, 300], fill=(255, 255, 255))  # 轻微差异（同场景加元素）
        im2.save(os.path.join(self.day, "A_variant.JPG"), "JPEG", quality=92)
        make_jpeg(os.path.join(self.day, "B_other.JPG"), color=(200, 40, 40))
        for _ in range(4):
            self.wb.scan_once()
        # sdcard 不在监听根，按真实用户流程走导入
        j = self.client.post("/api/import/scan", json={"path": self.sd}).get_json()
        self.client.post("/api/import/commit",
                         json={"paths": [c["path"] for c in j["candidates"]]})
        for r in self.rows("SELECT id FROM items"):
            self.wb.hash_one(r["id"])
            self.wb.thumb_one(r["id"])

    # ---- dhash 单元
    def test_dhash_unit(self):
        im = Image.new("RGB", (200, 200), (100, 100, 100))
        h1 = dhash(im)
        self.assertEqual(len(h1), 64)
        self.assertEqual(set(h1), {"0"})    # 纯灰图梯度全 0
        self.assertEqual(phash_distance(h1, h1), 0)
        g = im.convert("L").point(lambda v: min(255, v + 40))
        h2 = dhash(g.convert("RGB"))
        self.assertEqual(phash_distance(h1, h2), 0)  # 全局亮度平移不变
        self.assertEqual(phash_distance("0" * 64, "1" * 64), 64)

    # ---- phash 入库与跨目录相似
    def test_similar_across_dirs(self):
        self.seed_similar()
        rows = self.rows("SELECT id, rel_path, phash FROM items WHERE phash!=''")
        self.assertEqual(len(rows), 4)  # 全部算出 phash
        base = [r for r in rows if r["rel_path"].endswith("A_0001.JPG")][0]
        j = self.client.get(f"/api/items/{base['id']}?_x=1").get_json()  # 确认详情可用
        resp = self.client.get(f"/api/items/{base['id']}/similar?threshold=10")
        j = resp.get_json()
        ids = {x["id"] for x in j["items"]}
        by_suffix = {os.path.basename(r["rel_path"]): r["id"] for r in rows}
        # 同画面拷贝（距离 0）与轻微变体（小距离）都要在，且零距离排最前
        self.assertIn(by_suffix["A_copy.JPG"], ids)
        self.assertIn(by_suffix["A_variant.JPG"], ids)
        self.assertNotIn(by_suffix["B_other.JPG"], ids)  # 不同画面不该混入
        self.assertEqual(j["items"][0]["distance"], 0)
        self.assertTrue(j["items"][0]["rel_path"].endswith("A_copy.JPG"))
        # 高阈值排除检查
        resp = self.client.get(f"/api/items/{base['id']}/similar?threshold=1")
        j2 = resp.get_json()
        self.assertEqual([x["id"] for x in j2["items"]], [by_suffix["A_copy.JPG"]])

    # ---- 颜色门控：同结构异色不算相似（dhash 盲区）
    def test_similar_color_gate(self):
        im = Image.new("RGB", (640, 480), (10, 90, 180))
        d = ImageDraw.Draw(im)
        d.ellipse([80, 60, 560, 420], fill=(230, 200, 60))
        im.save(os.path.join(self.day, "C_blue.JPG"), "JPEG", quality=92)
        im3 = Image.new("RGB", (640, 480), (200, 40, 30))       # 同结构、异色
        d3 = ImageDraw.Draw(im3)
        d3.ellipse([80, 60, 560, 420], fill=(40, 200, 90))
        im3.save(os.path.join(self.day, "C_red.JPG"), "JPEG", quality=92)
        for _ in range(4):
            self.wb.scan_once()
        for r in self.rows("SELECT id FROM items"):
            self.wb.hash_one(r["id"])
            self.wb.thumb_one(r["id"])
        base = [r for r in self.rows("SELECT id, rel_path FROM items")
                if r["rel_path"].endswith("C_blue.JPG")][0]
        j = self.client.get(f"/api/items/{base['id']}/similar?threshold=12").get_json()
        found = [x for x in j["items"] if x["rel_path"].endswith("C_red.JPG")]
        self.assertEqual(found, [], "同结构异色应被颜色门控排除")

    # ---- 视频导出
    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_video_export(self):
        p = os.path.join(self.day, "V.MP4")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc2=duration=2:size=640x360:rate=24",
                        "-pix_fmt", "yuv420p", p], check=True, capture_output=True)
        make_jpeg(os.path.join(self.day, "P.JPG"))
        for _ in range(4):
            self.wb.scan_once()
        ids = [r["id"] for r in self.rows("SELECT id FROM items ORDER BY id")]
        job_id = self.wb.create_job("export", {"ids": ids, "opts": {
            "include_video": True, "video_crf": 24, "temp": 20, "vignette": 40,
            "watermark_text": "水印", "resize_w": 480}})
        self.wb.run_job(job_id)
        j = self.client.get(f"/api/jobs/{job_id}").get_json()
        self.assertEqual(j["status"], "done", j.get("note"))
        self.assertEqual(j["result"]["count"], 2)
        with zipfile.ZipFile(j["result"]["zip"]) as z:
            names = z.namelist()
            mp4s = [n for n in names if n.endswith(".mp4")]
            self.assertEqual(len(mp4s), 1)
            with z.open(mp4s[0]) as f:
                buf = f.read()
        probe_path = os.path.join(self.tmp, "probe-export.mp4")
        with open(probe_path, "wb") as f:
            f.write(buf)
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=codec_name,height",
                                "-print_format", "json", probe_path],
                               capture_output=True, text=True)
        import json as _json
        st = _json.loads(probe.stdout)["streams"][0]
        self.assertEqual(st["codec_name"], "h264")
        self.assertEqual(st["height"], 270)  # resize_w=480、源 16:9 → 480x270（-2 对齐不放大）
        # 视频导出列默认关：不勾选时视频被跳过
        job2 = self.wb.create_job("export", {"ids": ids, "opts": {}})
        self.wb.run_job(job2)
        j2 = self.client.get(f"/api/jobs/{job2}").get_json()
        self.assertEqual(j2["result"]["count"], 1)  # 只有照片

    # ---- jobs 列表
    def test_jobs_list(self):
        make_jpeg(os.path.join(self.day, "J.JPG"))
        for _ in range(4):
            self.wb.scan_once()
        jid = self.wb.create_job("ai_tag", {"ids": [1, 2, 3]})
        self.wb.run_job(jid)   # 未配置 AI → failed（也要出现在列表里）
        j = self.client.get("/api/jobs").get_json()
        self.assertGreaterEqual(len(j["jobs"]), 1)
        self.assertEqual(j["jobs"][0]["id"], jid)
        self.assertIn("payload", j["jobs"][0])

    # ---- 回填：模拟 v0.2 老库（thumb ready、phash 空）
    def test_phash_backfill(self):
        self.seed_similar()
        conn = self.wb.db()
        conn.execute("UPDATE items SET phash=''")
        conn.commit()
        conn.close()
        self.wb.backfill_phash()
        n = self.rows("SELECT COUNT(*) c FROM items WHERE phash!=''")[0]["c"]
        self.assertEqual(n, 4)


if __name__ == "__main__":
    unittest.main()
