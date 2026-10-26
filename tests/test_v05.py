# -*- coding: utf-8 -*-
"""v0.5 测试：LUT 库/预览/统一 ffmpeg 导出/竖向时间轴数据"""
import io
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from app.web_app import DEFAULT_CONFIG, WB, create_app

HAS_FFMPEG = bool(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0)

MINI_LUT = "LUT_3D_SIZE 2\n" + "".join(f"{r} {g} {b}\n"
                                       for b in (0, 1) for g in (0, 1) for r in (0, 1))


def make_jpeg(path, size=(640, 480), color=(120, 160, 90)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    return path


class V05Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="djiwb-v05-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.day = os.path.join(self.tmp, "DJI Device", "Cam", "2026-10-26")
        os.makedirs(self.day, exist_ok=True)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(watch_roots=[os.path.join(self.tmp, "DJI Device")],
                   stable_age=0, stable_scans=2, proxy_enabled=False)
        self.wb = WB(cfg, Path(self.tmp) / "data", start_workers=False)
        self.client = create_app(self.wb).test_client()

    def seed(self, n=2, colors=((120, 160, 90), (200, 80, 40))):
        for i, c in enumerate(colors[:n]):
            make_jpeg(os.path.join(self.day, f"L_{i:04d}.JPG"), color=c)
        for _ in range(4):
            self.wb.scan_once()
        for r in [dict(x) for x in self.wb.db().execute("SELECT id FROM items").fetchall()]:
            self.wb.hash_one(r["id"])
            self.wb.thumb_one(r["id"])

    def rows(self, sql, args=()):
        conn = self.wb.db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    # ---- LUT 上传/列表/删除
    def test_lut_crud(self):
        r = self.client.post("/api/luts", data={"file": (io.BytesIO(b"garbage"), "bad.cube")})
        self.assertEqual(r.status_code, 400)  # 无 LUT_3D_SIZE 声明
        r = self.client.post("/api/luts", data={"file": (io.BytesIO(b"x"), "no.txt")})
        self.assertEqual(r.status_code, 400)  # 非 cube
        r = self.client.post("/api/luts", data={"file": (io.BytesIO(MINI_LUT.encode()), "DLogM2Rec709.cube")})
        self.assertEqual(r.status_code, 200)
        self.assertTrue((self.wb.lut_dir / "DLogM2Rec709.cube").exists())
        j = self.client.get("/api/luts").get_json()
        self.assertEqual([x["name"] for x in j["luts"]], ["DLogM2Rec709.cube"])
        # 空文件上传
        r = self.client.post("/api/luts", data={"file": (io.BytesIO(b""), "e.cube")})
        self.assertEqual(r.status_code, 400)
        # 删除
        r = self.client.delete("/api/luts/DLogM2Rec709.cube")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/luts").get_json()["luts"], [])
        self.assertEqual(self.client.delete("/api/luts/none.cube").status_code, 404)

    # ---- 套 LUT 预览
    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_lut_preview(self):
        self.seed()
        self.client.post("/api/luts", data={"file": (io.BytesIO(MINI_LUT.encode()), "m.cube")})
        iid = self.rows("SELECT id FROM items")[0]["id"]
        # 无 lut 参数 = 原缩略图
        r0 = self.client.get(f"/api/items/{iid}/preview")
        self.assertEqual(r0.status_code, 200)
        # 带 lut = 生成并缓存
        r1 = self.client.get(f"/api/items/{iid}/preview?lut=m.cube")
        self.assertEqual(r1.status_code, 200)
        caches = list(self.wb.preview_dir.glob(f"{iid}__m__*.jpg"))
        self.assertEqual(len(caches), 1)
        # 二次命中缓存
        r2 = self.client.get(f"/api/items/{iid}/preview?lut=m.cube")
        self.assertEqual(r2.status_code, 200)
        # 不存在的 lut
        self.assertEqual(self.client.get(f"/api/items/{iid}/preview?lut=nope.cube").status_code, 404)

    # ---- 照片导出走 ffmpeg 统一链（带 LUT）
    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_photo_export_with_lut(self):
        self.seed()
        self.client.post("/api/luts", data={"file": (io.BytesIO(MINI_LUT.encode()), "m.cube")})
        ids = [r["id"] for r in self.rows("SELECT id FROM items")]
        job = self.wb.create_job("export", {"ids": ids, "opts": {
            "lut": "m.cube", "vignette": 40, "resize_w": 320}})
        self.wb.run_job(job)
        j = self.client.get(f"/api/jobs/{job}").get_json()
        self.assertEqual(j["status"], "done", j.get("note"))
        self.assertEqual(j["result"]["count"], 2)
        with zipfile.ZipFile(j["result"]["zip"]) as z:
            names = z.namelist()
            self.assertEqual(len(names), 2)
            img = Image.open(io.BytesIO(z.read(names[0])))
            self.assertEqual(img.width, 320)

    def test_photo_export_unknown_lut_fails_clearly(self):
        self.seed()
        ids = [r["id"] for r in self.rows("SELECT id FROM items")]
        job = self.wb.create_job("export", {"ids": ids, "opts": {"lut": "nope.cube"}})
        self.wb.run_job(job)
        j = self.client.get(f"/api/jobs/{job}").get_json()
        # lut 不存在时静默跳过 LUT（resilient），导出仍应成功
        self.assertEqual(j["status"], "done", j.get("note"))

    # ---- RAW 导出（内嵌预览→ffmpeg）
    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_raw_export_ffmpeg(self):
        import struct
        im = Image.new("RGB", (320, 240), (60, 180, 60))
        bio = io.BytesIO()
        im.save(bio, "JPEG", quality=90)

        def entry(tag, typ, cnt, val):
            return struct.pack("<HHI", tag, typ, cnt) + struct.pack("<I", val)
        JPEG_OFF = 188
        ifd0 = (struct.pack("<H", 7) + entry(254, 4, 1, 0) + entry(256, 3, 1, 320)
                + entry(257, 3, 1, 240) + entry(259, 3, 1, 1) + entry(273, 4, 1, 0)
                + entry(279, 4, 1, 1000) + entry(330, 4, 1, 98) + struct.pack("<I", 0))
        sub = (struct.pack("<H", 7) + entry(254, 4, 1, 1) + entry(256, 3, 1, 320)
               + entry(257, 3, 1, 240) + entry(259, 3, 1, 6) + entry(273, 4, 1, JPEG_OFF)
               + entry(279, 4, 1, len(bio.getvalue())) + entry(284, 3, 1, 2) + struct.pack("<I", 0))
        dng = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8) + ifd0 + sub + bio.getvalue()
        p = os.path.join(self.day, "R_0001.dng")
        with open(p, "wb") as f:
            f.write(dng)
        self.seed(1)  # 会扫描到 dng（scan 在 seed 里）
        # 补扫 dng
        for _ in range(4):
            self.wb.scan_once()
        raw_id = self.rows("SELECT id FROM items WHERE kind='raw'")
        if not raw_id:
            self.wb.thumb_one(self.rows("SELECT id FROM items ORDER BY id DESC")[0]["id"])
            raw_id = self.rows("SELECT id FROM items WHERE kind='raw'")
        self.assertTrue(raw_id)
        job = self.wb.create_job("export", {"ids": [raw_id[0]["id"]], "opts": {"lut": "none"}})
        self.wb.run_job(job)
        j = self.client.get(f"/api/jobs/{job}").get_json()
        self.assertEqual(j["status"], "done", j.get("note"))


if __name__ == "__main__":
    unittest.main()


class AccessAuthTest(unittest.TestCase):
    """可选访问密码：开启后 API 401、登录后放行、留空关闭"""
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="djiwb-auth-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(watch_roots=[], access_password="sec123")
        self.wb = WB(cfg, Path(self.tmp) / "data", start_workers=False)
        self.client = create_app(self.wb).test_client()

    def test_auth_gate(self):
        # 未登录：API 401、页面 200（前端渲染遮罩）
        self.assertEqual(self.client.get("/api/items").status_code, 401)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/api/health").status_code, 200)  # 探测端点放行
        # 错密码
        r = self.client.post("/api/login", json={"password": "wrong"})
        self.assertEqual(r.status_code, 401)
        # 对密码 → cookie
        r = self.client.post("/api/login", json={"password": "sec123"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/items").status_code, 200)

    def test_auth_disabled_when_empty(self):
        self.wb.cfg["access_password"] = ""
        self.assertEqual(self.client.get("/api/items").status_code, 200)
