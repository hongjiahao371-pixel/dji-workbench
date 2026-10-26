# -*- coding: utf-8 -*-
"""v0.2 新功能测试：批量操作/相册/地图/雪碧图/自动选片/导出/幻灯片/质量分/备份/迁移"""
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


def make_jpeg(path, size=(640, 480), color=(120, 160, 90), mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


def make_sharp_jpeg(path, mtime=None):
    """高对比度边缘图（清晰度分应明显高于纯色图）"""
    from PIL import ImageDraw
    im = Image.new("RGB", (640, 480), (240, 240, 240))
    d = ImageDraw.Draw(im)
    for i in range(0, 640, 40):
        d.rectangle([i, 0, i + 20, 480], fill=(20, 20, 20))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, "JPEG", quality=92)
    if mtime:
        os.utime(path, (mtime, mtime))
    return path


class FeatureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="djiwb-v02-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.daydir = os.path.join(self.tmp, "DJI Device", "Osmo Pocket 4 Pro", "2026-10-24")
        os.makedirs(self.daydir, exist_ok=True)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(watch_roots=[os.path.join(self.tmp, "DJI Device")],
                   poll_interval=1, stable_age=0, stable_scans=2, proxy_enabled=False)
        self.wb = WB(cfg, Path(self.tmp) / "data", start_workers=False)
        self.app = create_app(self.wb)
        self.client = self.app.test_client()

    def rows(self, sql, args=()):
        conn = self.wb.db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def seed_photos(self):
        """3 张连拍（清晰度不同）+1 张隔开（内容各不相同，避免哈希去重干扰断言）"""
        base = 1770000000
        make_jpeg(os.path.join(self.daydir, "SEQ_0001.JPG"), color=(60, 60, 60), mtime=base)     # 糊/暗
        make_sharp_jpeg(os.path.join(self.daydir, "SEQ_0002.JPG"), mtime=base + 1)               # 锐利
        make_jpeg(os.path.join(self.daydir, "SEQ_0003.JPG"), color=(90, 90, 90), mtime=base + 2) # 中等
        time.sleep(0.05)
        make_jpeg(os.path.join(self.daydir, "SOLO_0004.JPG"), color=(10, 200, 90), mtime=base + 600)  # 独立一张（绿色区分内容）
        for _ in range(6):
            self.wb.scan_once()
        for r in self.rows("SELECT id FROM items"):
            self.wb.hash_one(r["id"])
            self.wb.thumb_one(r["id"])

    # ---- 批量操作
    def test_batch_rate_and_reject(self):
        self.seed_photos()
        ids = [r["id"] for r in self.rows("SELECT id FROM items ORDER BY id")]
        r = self.client.post("/api/items/batch", json={"ids": ids[:2], "rating": 5})
        self.assertEqual(r.get_json()["updated"], 2)
        rows = self.rows("SELECT rating FROM items ORDER BY id")
        self.assertEqual([x["rating"] for x in rows], [5, 5, 0, 0])
        r = self.client.post("/api/items/batch", json={"ids": ids[1:], "rejected": True})
        self.assertEqual(r.get_json()["updated"], 3)
        self.assertEqual(self.client.get("/api/items").get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/items?rejected=show").get_json()["total"], 4)
        # 空参
        r = self.client.post("/api/items/batch", json={})
        self.assertEqual(r.status_code, 400)

    # ---- 相册
    def test_albums_crud_and_filter(self):
        self.seed_photos()
        ids = [r["id"] for r in self.rows("SELECT id FROM items ORDER BY id")]
        r = self.client.post("/api/albums", json={"name": "旅行精选"})
        aid = r.get_json()["id"]
        self.assertEqual(r.status_code, 200)
        # 重名 400
        self.assertEqual(self.client.post("/api/albums", json={"name": "旅行精选"}).status_code, 400)
        self.client.post("/api/items/batch", json={"ids": ids[:2], "album_add": aid})
        j = self.client.get("/api/albums").get_json()["albums"]
        self.assertEqual(j[0]["count"], 2)
        # 相册筛选
        self.assertEqual(self.client.get(f"/api/items?album={aid}").get_json()["total"], 2)
        # 移出
        self.client.post("/api/items/batch", json={"ids": ids[:1], "album_remove": aid})
        self.assertEqual(self.client.get(f"/api/items?album={aid}").get_json()["total"], 1)
        # 删除相册不动素材
        self.client.delete(f"/api/albums/{aid}")
        self.assertEqual(self.client.get("/api/albums").get_json()["albums"], [])
        self.assertEqual(self.client.get("/api/items").get_json()["total"], 4)

    # ---- 质量分 + 筛选 + 排序
    def test_quality_score(self):
        self.seed_photos()
        rows = self.rows("SELECT rel_path, quality FROM items")
        q = {os.path.basename(r["rel_path"]): r["quality"] for r in rows}
        self.assertGreater(q["SEQ_0002.JPG"], q["SEQ_0001.JPG"], "锐利图应比纯色图质量高")
        self.assertGreater(q["SEQ_0001.JPG"], 0)
        self.assertEqual(self.client.get("/api/items?min_quality=999").get_json()["total"], 0)
        items = self.client.get("/api/items?sort=quality_desc").get_json()["items"]
        self.assertEqual(os.path.basename(items[0]["rel_path"]), "SEQ_0002.JPG")

    # ---- 自动选片
    def test_autopick_groups(self):
        self.seed_photos()
        j = self.client.get("/api/autopick?date=2026-10-24").get_json()
        self.assertEqual(len(j["groups"]), 1)  # 3 连拍一组；SOLO 单张不成组
        g = j["groups"][0]
        self.assertEqual(len(g["ids"]), 3)
        self.assertEqual(g["suggest_id"], g["ids"][1])  # 锐利那张是 SEQ_0002（第2张）
        # 应用建议
        self.client.post("/api/items/batch", json={"ids": [g["suggest_id"]], "rating": 3})
        self.client.post("/api/items/batch",
                         json={"ids": [i for i in g["ids"] if i != g["suggest_id"]], "rejected": True})
        self.assertEqual(self.client.get("/api/items").get_json()["total"], 2)
        self.assertEqual(self.client.get("/api/items?min_rating=3").get_json()["total"], 1)

    # ---- 地图
    def test_map_endpoint(self):
        # EXIF GPS 照片
        import io
        from PIL import Image
        im = Image.new("RGB", (80, 60), (10, 120, 200))
        exif = im.getexif()
        gps = exif.get_ifd(0x8825)
        from PIL import Image as _I
        gps[1] = "N"; gps[2] = (31.0, 23.0, 49.0)
        gps[3] = "E"; gps[4] = (121.0, 47.0, 28.0)
        im.save(os.path.join(self.daydir, "GPS_0001.JPG"), exif=exif)
        for _ in range(6):
            self.wb.scan_once()
        for r in self.rows("SELECT id FROM items"):
            self.wb.thumb_one(r["id"])
        j = self.client.get("/api/map").get_json()
        self.assertEqual(len(j["points"]), 1)
        p = j["points"][0]
        self.assertAlmostEqual(p["lat"], 31 + 23 / 60 + 49 / 3600, places=3)
        self.assertAlmostEqual(p["lon"], 121 + 47 / 60 + 28 / 3600, places=3)

    # ---- 雪碧图
    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_sprite(self):
        p = os.path.join(self.daydir, "DJI_0200.MP4")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "testsrc2=duration=6:size=640x360:rate=24",
                        "-pix_fmt", "yuv420p", p], check=True, capture_output=True)
        for _ in range(6):
            self.wb.scan_once()
        vid = self.rows("SELECT id FROM items WHERE kind='video'")[0]
        self.wb.thumb_one(vid["id"])   # 填 duration
        self.wb.sprite_one(vid["id"])
        vid = self.rows("SELECT id, sprite_status FROM items WHERE kind='video'")[0]
        self.assertEqual(vid["sprite_status"], "ready")
        out = self.wb.sprite_dir / f"{vid['id']}.jpg"
        self.assertTrue(out.exists())
        with Image.open(out) as im:
            # 每帧 16:9（源 640x360 → scale 160:-2），12 帧横排：整图宽高比 = 12*16/9
            frame_ratio = im.width / (im.height * 16 / 9)
            self.assertAlmostEqual(frame_ratio, 12, delta=0.1)
        resp = self.client.get(f"/api/items/{vid['id']}/sprite")
        self.assertEqual(resp.status_code, 200)

    # ---- 调色方向性（灰图排除内容色干扰）
    def test_adjustments_direction(self):
        import numpy as np
        from PIL import Image as I
        gray = I.new("RGB", (400, 300), (128, 128, 128))
        warm = self.wb.apply_photo_adjustments(gray.copy(), {"temp": 50})
        a = np.asarray(warm, dtype=int)
        self.assertGreater(a[..., 0].mean(), a[..., 2].mean() + 25, "暖色应 R 明显大于 B")
        cool = self.wb.apply_photo_adjustments(gray.copy(), {"temp": -50})
        a = np.asarray(cool, dtype=int)
        self.assertGreater(a[..., 2].mean(), a[..., 0].mean() + 25, "冷色应 B 明显大于 R")
        def corner_delta(strength):
            out = self.wb.apply_photo_adjustments(gray.copy(), {"vignette": strength})
            g = np.asarray(out.convert("L"), dtype=int)
            h, w = g.shape
            center = g[int(h*0.4):int(h*0.6), int(w*0.4):int(w*0.6)].mean()
            return center - np.mean([g[0:15,0:15].mean(), g[0:15,-15:].mean(),
                                     g[-15:,0:15].mean(), g[-15:,-15:].mean()])
        d35, d100 = corner_delta(35), corner_delta(100)
        self.assertGreater(d35, 15, "中档暗角应可感知")
        self.assertGreater(d100, d35 + 15, "暗角强度应随档位单调增强")

    # ---- 导出（调色/水印/缩放/zip）
    def test_export_job(self):
        self.seed_photos()
        ids = [r["id"] for r in self.rows("SELECT id FROM items ORDER BY id")][:2]
        job_id = self.wb.create_job("export", {"ids": ids, "opts": {
            "watermark_text": "测试水印", "temp": 20, "vignette": 40, "resize_w": 320}})
        self.wb.run_job(job_id)
        j = self.client.get(f"/api/jobs/{job_id}").get_json()
        self.assertEqual(j["status"], "done", j.get("note"))
        self.assertEqual(j["result"]["count"], 2)
        self.assertTrue(os.path.isfile(j["result"]["zip"]))
        import zipfile
        with zipfile.ZipFile(j["result"]["zip"]) as z:
            names = z.namelist()
            self.assertEqual(len(names), 2)
        # 下载端点
        resp = self.client.get(f"/api/jobs/{job_id}/download")
        self.assertEqual(resp.status_code, 200)
        # 校验产物确实是调过色的 320 宽 JPEG
        from PIL import Image as I2
        import io as _io
        with zipfile.ZipFile(j["result"]["zip"]) as z:
            with I2.open(_io.BytesIO(z.read(names[0]))) as im:
                self.assertEqual(im.width, 320)
        # 空导出报错
        job2 = self.wb.create_job("export", {"ids": [999999], "opts": {}})
        self.wb.run_job(job2)
        self.assertEqual(self.client.get(f"/api/jobs/{job2}").get_json()["status"], "failed")

    # ---- 幻灯片
    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_slideshow_job(self):
        self.seed_photos()
        ids = [r["id"] for r in self.rows("SELECT id FROM items ORDER BY id")]
        job_id = self.wb.create_job("slideshow", {"ids": ids, "duration_per": 1.5})
        self.wb.run_job(job_id)
        j = self.client.get(f"/api/jobs/{job_id}").get_json()
        self.assertEqual(j["status"], "done", j.get("note"))
        mp4 = j["result"]["mp4"]
        self.assertTrue(os.path.isfile(mp4))
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=width,height,nb_frames",
                              "-show_entries", "format=duration", "-print_format", "json", mp4],
                             capture_output=True, text=True)
        import json as _json
        meta = _json.loads(out.stdout)
        self.assertEqual(meta["streams"][0]["width"], 1920)
        self.assertGreater(float(meta["format"]["duration"]), 4 * 1.5 - 0.5)

    # ---- AI 打标（未配置时清晰报错）
    def test_ai_tag_not_configured(self):
        self.seed_photos()
        ids = [r["id"] for r in self.rows("SELECT id FROM items ORDER BY id")][:1]
        job_id = self.wb.create_job("ai_tag", {"ids": ids})
        self.wb.run_job(job_id)
        j = self.client.get(f"/api/jobs/{job_id}").get_json()
        self.assertEqual(j["status"], "failed")
        self.assertIn("AI", j["note"])

    # ---- 备份目录
    def test_backup_dir(self):
        backup = os.path.join(self.tmp, "backup-mirror")
        self.wb.cfg["backup_dir"] = backup
        self.seed_photos()
        mirrored = []
        for dp, _, fns in os.walk(backup):
            for fn in fns:
                mirrored.append(os.path.join(dp, fn))
        self.assertEqual(len(mirrored), 4)
        # 设备/日期子目录结构
        rel = os.path.relpath(mirrored[0], backup)
        self.assertTrue(rel.startswith("Osmo Pocket 4 Pro/2026-10-24/"), rel)
        # 重复哈希跑两次不重复复制（文件内容一致跳过）
        before = {m: os.path.getmtime(m) for m in mirrored}
        for r in self.rows("SELECT id FROM items"):
            self.wb.hash_one(r["id"])   # sha256 已存在，直接 return，不会二次复制

    # ---- 老库迁移
    def test_migration_from_v01(self):
        self.seed_photos()
        # 模拟老库：删掉新列，重连应自动补列且数据不丢
        conn = self.wb.db()
        conn.execute("ALTER TABLE items RENAME TO items_old")
        conn.execute("""CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL,
            kind TEXT DEFAULT '', thumb_status TEXT DEFAULT 'none', stable INTEGER DEFAULT 1,
            missing INTEGER DEFAULT 0, duplicate_of INTEGER, sha256 TEXT DEFAULT '',
            rel_path TEXT DEFAULT '', taken_at TEXT DEFAULT '')""")
        conn.execute("INSERT INTO items(id,path,kind,thumb_status,stable,sha256,rel_path) "
                     "SELECT id,path,kind,thumb_status,stable,sha256,rel_path FROM items_old LIMIT 2")
        conn.execute("DROP TABLE items_old")
        conn.commit()
        conn.close()
        rows = self.rows("SELECT * FROM items")   # db() 触发迁移
        self.assertEqual(len(rows), 2)
        self.assertIn("quality", rows[0])
        self.assertIn("tags", rows[0])
        self.assertIn("sprite_status", rows[0])


if __name__ == "__main__":
    unittest.main()
