# -*- coding: utf-8 -*-
"""流程测试：扫描入库 / 缩略图 / 去重 / API（临时目录 + 真实小文件）"""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from app.web_app import DEFAULT_CONFIG, WB, create_app

HAS_FFMPEG = bool(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0)


def make_jpeg(path, size=(640, 480), color=(120, 160, 90)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    return path


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="djiwb-flow-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.media = os.path.join(self.tmp, "DJI Device")
        self.daydir = os.path.join(self.media, "Osmo Pocket 4 Pro", "2026-10-24")
        os.makedirs(self.daydir, exist_ok=True)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(watch_roots=[self.media], poll_interval=1, stable_age=0,
                   stable_scans=2, proxy_enabled=False)
        self.wb = WB(cfg, Path(self.tmp) / "data", start_workers=False)
        self.app = create_app(self.wb)
        self.client = self.app.test_client()

    # ---- helpers
    def scan_until_stable(self, max_rounds=6):
        for _ in range(max_rounds):
            self.wb.scan_once()
            conn = self.wb.db()
            try:
                rows = conn.execute("SELECT id, stable FROM items WHERE stable=0").fetchall()
                if not rows:
                    return
            finally:
                conn.close()
            time.sleep(0.05)
        self.fail("文件始终未判稳定")

    def rows(self, sql, args=()):
        conn = self.wb.db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    # ---- 扫描与稳定判定
    def test_scan_and_stability(self):
        p = make_jpeg(os.path.join(self.daydir, "DJI_0001.JPG"))
        s = self.wb.scan_once()
        self.assertEqual(s["new"], 1)
        rows = self.rows("SELECT * FROM items")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["stable"], 0)
        self.assertEqual(r["device"], "Osmo Pocket 4 Pro")
        self.assertEqual(r["day_folder"], "2026-10-24")
        self.assertEqual(r["kind"], "photo")
        self.assertTrue(r["taken_at"].startswith("2026-10-24 "))
        self.scan_until_stable()
        r = self.rows("SELECT * FROM items")[0]
        self.assertEqual(r["stable"], 1)
        self.assertTrue(os.path.isfile(p))

    def test_skip_temp_and_hidden(self):
        make_jpeg(os.path.join(self.daydir, "DJI_0002.JPG"))
        make_jpeg(os.path.join(self.daydir, ".DJI_0003.JPG"))
        make_jpeg(os.path.join(self.daydir, "._DJI_0004.JPG"))
        make_jpeg(os.path.join(self.daydir, "DJI_0005.jpg.part"))  # 下载/上传中间态，跳过
        self.wb.scan_once()
        rows = self.rows("SELECT path FROM items")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["path"].endswith("DJI_0002.JPG"))

    def test_pano_reclassify_by_ratio(self):
        make_jpeg(os.path.join(self.daydir, "PANO_0001.JPG"), size=(1600, 600))
        self.scan_until_stable()
        r = self.rows("SELECT * FROM items")[0]
        self.wb.thumb_one(r["id"])
        r = self.rows("SELECT * FROM items")[0]
        self.assertEqual(r["kind"], "pano")
        self.assertEqual((r["width"], r["height"]), (1600, 600))

    def test_missing_flag_when_file_gone(self):
        p = make_jpeg(os.path.join(self.daydir, "DJI_0006.JPG"))
        self.scan_until_stable()
        os.remove(p)
        self.wb.scan_once()
        r = self.rows("SELECT * FROM items")[0]
        self.assertEqual(r["missing"], 1)

    # ---- 缩略图与探测
    def test_thumb_photo(self):
        make_jpeg(os.path.join(self.daydir, "DJI_0007.JPG"), size=(800, 600))
        self.scan_until_stable()
        r = self.rows("SELECT * FROM items")[0]
        self.wb.thumb_one(r["id"])
        r = self.rows("SELECT * FROM items")[0]
        self.assertEqual(r["thumb_status"], "ready")
        self.assertEqual((r["width"], r["height"]), (800, 600))
        self.assertTrue(os.path.isfile(os.path.join(self.wb.thumb_dir, f"{r['id']}.jpg")))

    def test_thumb_raw_uses_embedded_preview(self):
        # 用假 DNG（内嵌 JPEG 预览）走 raw 分支
        import io
        from tests.test_media import build_fake_dng
        im = Image.new("RGB", (64, 48), (10, 200, 30))
        bio = io.BytesIO()
        im.save(bio, "JPEG", quality=90)
        dng = build_fake_dng(bio.getvalue(), jpeg_off=176)
        p = os.path.join(self.daydir, "DJI_0008.dng")
        with open(p, "wb") as f:
            f.write(dng)
        self.scan_until_stable()
        r = self.rows("SELECT * FROM items")[0]
        self.assertEqual(r["kind"], "raw")
        self.wb.thumb_one(r["id"])
        r = self.rows("SELECT * FROM items")[0]
        self.assertEqual(r["thumb_status"], "ready")

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_video_probe_and_thumb(self):
        p = os.path.join(self.daydir, "DJI_0009.MP4")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=10",
                        "-pix_fmt", "yuv420p", p], check=True, capture_output=True)
        self.scan_until_stable()
        r = self.rows("SELECT * FROM items")[0]
        self.assertEqual(r["kind"], "video")
        self.wb.thumb_one(r["id"])
        r = self.rows("SELECT * FROM items")[0]
        self.assertEqual(r["thumb_status"], "ready")
        self.assertEqual(r["width"], 320)
        self.assertGreater(r["duration"], 0.5)
        self.assertEqual(r["codec"], "h264")
        self.assertTrue(os.path.isfile(os.path.join(self.wb.thumb_dir, f"{r['id']}.jpg")))

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_proxy_transcode(self):
        self.wb.cfg["proxy_enabled"] = True
        p = os.path.join(self.daydir, "DJI_0010.MP4")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=10",
                        "-pix_fmt", "yuv420p", p], check=True, capture_output=True)
        self.scan_until_stable()
        r = self.rows("SELECT * FROM items")[0]
        self.wb.thumb_one(r["id"])
        self.wb.proxy_one(r["id"])
        r = self.rows("SELECT * FROM items")[0]
        self.assertEqual(r["proxy_status"], "ready")
        proxy = os.path.join(self.wb.proxy_dir, f"{r['id']}.mp4")
        self.assertTrue(os.path.isfile(proxy))
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=codec_name,height",
                              "-print_format", "json", "-print_format", "json", proxy],
                             capture_output=True, text=True)
        import json as _json
        st = _json.loads(out.stdout)["streams"][0]
        self.assertEqual(st["codec_name"], "h264")
        self.assertEqual(st["height"], 360)  # 源高 360 < 1080，不放大

    # ---- 哈希与去重
    def test_dedup_by_hash(self):
        src = make_jpeg(os.path.join(self.daydir, "DJI_0011.JPG"))
        other_day = os.path.join(self.media, "Osmo Pocket 4 Pro", "2026-10-25")
        dst = make_jpeg(os.path.join(other_day, "DJI_0011_copy.JPG"), color=(120, 160, 90))
        self.scan_until_stable()
        for r in self.rows("SELECT id FROM items"):
            self.wb.hash_one(r["id"])
        rows = self.rows("SELECT * FROM items ORDER BY id")
        self.assertEqual(rows[0]["sha256"], rows[1]["sha256"])
        dups = [r for r in rows if r["duplicate_of"]]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["duplicate_of"], rows[0]["id"])
        # 库内筛选默认排除重复
        resp = self.client.get("/api/items?duplicates=show")
        self.assertEqual(resp.get_json()["total"], 2)
        resp = self.client.get("/api/items")
        self.assertEqual(resp.get_json()["total"], 1)
        self.assertTrue(os.path.isfile(src) and os.path.isfile(dst))

    # ---- API
    def seed(self, n=3):
        for i in range(n):
            make_jpeg(os.path.join(self.daydir, f"SEED_{i:04d}.JPG"),
                      color=(60 + i * 40, 100, 140))
        self.scan_until_stable()
        for r in self.rows("SELECT id FROM items"):
            self.wb.hash_one(r["id"])
            self.wb.thumb_one(r["id"])

    def test_api_health_stats_items(self):
        self.seed()
        h = self.client.get("/api/health").get_json()
        self.assertTrue(h["ok"])
        self.assertEqual(h["items"], 3)
        st = self.client.get("/api/stats").get_json()
        self.assertEqual(st["by_kind"]["photo"], 3)
        self.assertEqual(st["devices"][0]["name"], "Osmo Pocket 4 Pro")
        items = self.client.get("/api/items").get_json()
        self.assertEqual(items["total"], 3)
        one = items["items"][0]
        self.assertIn("thumb_url", one)
        self.assertTrue(one["thumb_url"])

    def test_api_filters_and_rating(self):
        self.seed()
        ids = [r["id"] for r in self.rows("SELECT id FROM items ORDER BY id")]
        r = self.client.post(f"/api/items/{ids[0]}/rate", json={"rating": 5})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/items?min_rating=4").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/items?min_rating=4").get_json()["items"][0]["id"], ids[0])
        # 单独淘汰
        self.client.post(f"/api/items/{ids[0]}/rate", json={"rejected": True})
        self.assertEqual(self.client.get("/api/items?rejected=hide").get_json()["total"], 2)
        self.assertEqual(self.client.get("/api/items?rejected=only").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/items?rejected=only").get_json()["items"][0]["rating"], 5)
        self.assertEqual(self.client.get("/api/items?rejected=show").get_json()["total"], 3)
        self.assertEqual(self.client.get("/api/items?kind=video").get_json()["total"], 0)
        self.assertEqual(self.client.get("/api/items?kind=photo&rejected=show").get_json()["total"], 3)
        d = self.client.get(f"/api/items/{ids[0]}").get_json()
        self.assertEqual(d["rating"], 5)
        self.assertEqual(d["rejected"], 1)
        r404 = self.client.post("/api/items/999999/rate", json={"rating": 1})
        self.assertEqual(r404.status_code, 404)

    def test_api_pagination_and_sort(self):
        self.seed(4)
        r = self.client.get("/api/items?per_page=10&page=1").get_json()
        self.assertEqual(r["total"], 4)
        r = self.client.get("/api/items?per_page=2&page=2").get_json()
        self.assertEqual(len(r["items"]), 2)
        ids_desc = [i["id"] for i in self.client.get("/api/items?sort=date_desc").get_json()["items"]]
        ids_asc = [i["id"] for i in self.client.get("/api/items?sort=date_asc").get_json()["items"]]
        self.assertEqual(ids_desc, list(reversed(ids_asc)))

    def test_api_serve_thumb(self):
        self.seed()
        r = self.rows("SELECT id FROM items LIMIT 1")[0]
        resp = self.client.get(f"/api/items/{r['id']}/thumb")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "image/jpeg")

    def test_api_import_flow(self):
        self.seed()
        imp = os.path.join(self.tmp, "sdcard", "DCIM", "100MEDIA")
        make_jpeg(os.path.join(imp, "NEW_0001.JPG"), color=(10, 20, 30))
        make_jpeg(os.path.join(imp, "NEW_0002.JPG"), color=(200, 20, 30))
        # 把库里已有的一张拷进 SD 目录，验证去重跳过
        known = self.rows("SELECT path FROM items LIMIT 1")[0]["path"]
        shutil.copy(known, os.path.join(imp, "DUP_0001.JPG"))
        resp = self.client.post("/api/import/scan", json={"path": imp})
        j = resp.get_json()
        # 导入扫描按「路径」去重：库里已有文件即使拷贝到 SD 目录也算新候选，
        # 内容级去重在后台哈希阶段完成（duplicate_of）
        self.assertEqual(len(j["candidates"]), 3)
        self.assertEqual(j["already_known"], 0)
        resp = self.client.post("/api/import/commit",
                                json={"paths": [c["path"] for c in j["candidates"]]})
        self.assertEqual(resp.get_json()["added"], 3)
        # 重复 commit 不应再加
        resp = self.client.post("/api/import/commit",
                                json={"paths": [c["path"] for c in j["candidates"]]})
        self.assertEqual(resp.get_json()["added"], 0)
        # 导入的文件不在监听根，扫描不应把它们误标 missing
        self.wb.scan_once()
        imp_rows = self.rows("SELECT * FROM items WHERE source='import'")
        self.assertEqual(len(imp_rows), 3)
        self.assertTrue(all(r["missing"] == 0 for r in imp_rows))
        # 内容级去重：DUP_0001.JPG（库里已有同内容文件）哈希后应被标记重复
        dup_row = [r for r in imp_rows if "DUP_0001" in r["path"]][0]
        self.wb.hash_one(dup_row["id"])
        dup_row = self.rows("SELECT * FROM items WHERE id=?", (dup_row["id"],))[0]
        self.assertIsNotNone(dup_row["duplicate_of"])
        self.assertEqual(self.client.get("/api/items?duplicates=show").get_json()["total"], 6)

    def test_api_browse(self):
        j = self.client.get("/api/browse?path=/nonexistent-xyz").get_json()
        self.assertIn("error", j)
        j = self.client.get(f"/api/browse?path={self.tmp}").get_json()
        names = [d["name"] for d in j["dirs"]]
        self.assertIn("DJI Device", names)

    def test_api_config_roundtrip(self):
        r = self.client.post("/api/config", json={"poll_interval": 33, "proxy_crf": 20,
                                                  "watch_roots": ["/x/y"]})
        self.assertEqual(r.status_code, 200)
        cfg = self.client.get("/api/config").get_json()
        self.assertEqual(cfg["poll_interval"], 33)
        self.assertEqual(cfg["proxy_crf"], 20)
        self.assertEqual(cfg["watch_roots"], ["/x/y"])
        # 非法值回落
        self.client.post("/api/config", json={"poll_interval": "abc"})
        cfg = self.client.get("/api/config").get_json()
        self.assertEqual(cfg["poll_interval"], 33)


if __name__ == "__main__":
    unittest.main()
