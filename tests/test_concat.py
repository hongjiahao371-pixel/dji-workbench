# -*- coding: utf-8 -*-
"""拼接台测试：同规格无损 copy / 混规格标准化 / trims 掐头去尾 / 校验拒绝"""
import os
import shutil
import subprocess
import json as _json
import tempfile
import unittest
from pathlib import Path

from app.web_app import DEFAULT_CONFIG, WB, create_app

HAS_FFMPEG = bool(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0)


def make_video(path, seconds=2, size="320x240", rate=24, color=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    src = f"testsrc2=duration={seconds}:size={size}:rate={rate}"
    if color:
        src = f"color=c={color}:size={size}:rate={rate}:duration={seconds}"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", src,
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", path],
                   check=True, capture_output=True)
    return path


class ConcatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="djiwb-concat-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.day = os.path.join(self.tmp, "DJI Device", "Cam", "2026-10-25")
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

    def scan_videos(self):
        for _ in range(4):
            self.wb.scan_once()
        ids = [r["id"] for r in self.rows(
            "SELECT id FROM items WHERE kind='video' ORDER BY id")]
        return ids

    def vid(self, job_id):
        conn = self.wb.db()
        try:
            r = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()
        d = dict(r)
        if d.get("result"):
            try: d["result"] = _json.loads(d["result"])
            except ValueError: pass
        return d

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_concat_uniform_copy(self):
        """同规格两段 → 无损拼接，输出时长≈两段之和"""
        make_video(os.path.join(self.day, "A.MP4"), seconds=2)
        make_video(os.path.join(self.day, "B.MP4"), seconds=3)
        ids = self.scan_videos()
        self.assertEqual(len(ids), 2)
        job_id = self.wb.create_job("concat", {"ids": ids})
        self.wb.run_job(job_id)
        j = self.vid(job_id)
        self.assertEqual(j["status"], "done", j["note"])
        r = j["result"]
        self.assertEqual(r["count"], 2)
        self.assertEqual(r["mode"], "无损拼接")
        self.assertGreaterEqual(r["seconds"], 4.5)
        self.assertTrue(os.path.isfile(r["mp4"]))
        # 下载端点可用
        resp = self.client.get(f"/api/jobs/{job_id}/download")
        self.assertEqual(resp.status_code, 200)

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_concat_mixed_normalize(self):
        """混规格（分辨率不同）→ 标准化转码，画幅以第一段为准"""
        make_video(os.path.join(self.day, "A.MP4"), seconds=2, size="320x240")
        make_video(os.path.join(self.day, "B.MP4"), seconds=2, size="640x360")
        ids = self.scan_videos()
        job_id = self.wb.create_job("concat", {"ids": ids})
        self.wb.run_job(job_id)
        j = self.vid(job_id)
        self.assertEqual(j["status"], "done", j["note"])
        self.assertEqual(j["result"]["mode"], "标准化拼接")
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-of", "json", j["result"]["mp4"]],
                           capture_output=True, timeout=60)
        import json as _json
        st = _json.loads(p.stdout)["streams"][0]
        self.assertEqual((st["width"], st["height"]), (320, 240))

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_concat_trims(self):
        """掐头去尾：两段各留 1s → 成片时长明显小于原和"""
        make_video(os.path.join(self.day, "A.MP4"), seconds=3)
        make_video(os.path.join(self.day, "B.MP4"), seconds=3)
        ids = self.scan_videos()
        job_id = self.wb.create_job("concat", {
            "ids": ids,
            "trims": [{"id": ids[0], "start": 1, "end": 2},
                      {"id": ids[1], "start": 0, "end": 1}]})
        self.wb.run_job(job_id)
        j = self.vid(job_id)
        self.assertEqual(j["status"], "done", j["note"])
        self.assertLessEqual(j["result"]["seconds"], 2.6)

    def test_concat_single_trim_only(self):
        """单段 + 掐头去尾 = 修剪（单段语义允许，见 test_v08）"""
        make_video(os.path.join(self.day, "A.MP4"), seconds=2)
        ids = self.scan_videos()
        resp = self.client.post("/api/jobs", json={
            "kind": "concat", "ids": ids[:1],
            "trims": [{"id": ids[0], "start": 0.5, "end": 1.5}]})
        self.assertEqual(resp.status_code, 200)
        job_id = resp.get_json()["job_id"]
        self.wb.run_job(job_id)
        j = self.vid(job_id)
        self.assertEqual(j["status"], "done", j["note"])
        self.assertLessEqual(j["result"]["seconds"], 1.4)

    def test_concat_rejects_kind(self):
        """照片不能进拼接"""
        from PIL import Image
        Image.new("RGB", (320, 240), (90, 140, 60)).save(
            os.path.join(self.day, "P.JPG"), "JPEG", quality=90)
        for _ in range(4):
            self.wb.scan_once()
        ids = [r["id"] for r in self.rows(
            "SELECT id FROM items WHERE kind='photo' ORDER BY id")]
        resp = self.client.post("/api/jobs", json={"kind": "concat", "ids": ids[:2]})
        self.assertEqual(resp.status_code, 200)
        job_id = resp.get_json()["job_id"]
        self.wb.run_job(job_id)
        self.assertEqual(self.vid(job_id)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
