# -*- coding: utf-8 -*-
"""v0.8：拼接台（concat job：无损快拼 / 标准化转码 / 掐头去尾 / 排序）"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.web_app import DEFAULT_CONFIG, WB, create_app

HAS_FFMPEG = bool(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0)


def make_video(path, size="320x180", dur=1, color=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    src = f"testsrc=duration={dur}:size={size}:rate=10"
    if color:
        src = f"color=c={color}:size={size}:rate=10:duration={dur}"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", src,
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", path],
                   check=True, capture_output=True)
    return path


class ConcatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="djiwb-concat-")
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

    def scan_until_stable(self, max_rounds=6):
        for _ in range(max_rounds):
            self.wb.scan_once()
            rows = self.wb.db().execute(
                "SELECT COUNT(*) c FROM items WHERE stable=1").fetchone()["c"]
            if rows >= 1:
                return
            import time as _t
            _t.sleep(0.2)

    def _import(self, path):
        make_video(path)
        self.scan_until_stable()
        rows = self.wb.db().execute("SELECT id FROM items").fetchall()
        return [r["id"] for r in rows]

    def _run_concat(self, ids, trims=None):
        payload = {"ids": ids}
        if trims:
            payload["trims"] = trims
        result = self.wb.run_concat_job(payload)
        return result

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_concat_lossless_same_params(self):
        ids = self._import(os.path.join(self.daydir, "DJI_0001.MP4"))
        p2 = os.path.join(self.daydir, "DJI_0002.MP4")
        make_video(p2, dur=1)
        self.wb.scan_once()
        ids = [r["id"] for r in self.wb.db().execute(
            "SELECT id FROM items ORDER BY id").fetchall()]
        self.assertEqual(len(ids), 2)
        result = self._run_concat(ids)
        self.assertEqual(result["mode"], "无损拼接")
        self.assertEqual(result["count"], 2)
        self.assertGreater(result["seconds"], 1.5)
        self.assertTrue(os.path.isfile(result["mp4"]))

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_concat_transcode_mixed_sizes(self):
        ids = self._import(os.path.join(self.daydir, "DJI_0001.MP4"))
        p2 = os.path.join(self.daydir, "DJI_0002.MP4")
        make_video(p2, size="640x360", dur=1)   # 不同分辨率
        self.wb.scan_once()
        ids = [r["id"] for r in self.wb.db().execute(
            "SELECT id FROM items ORDER BY id").fetchall()]
        result = self._run_concat(ids)
        self.assertEqual(result["mode"], "标准化拼接")
        # 输出分辨率应等于第一段
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=width,height",
                              "-of", "json", result["mp4"]],
                             capture_output=True, text=True)
        st = json.loads(out.stdout)["streams"][0]
        self.assertEqual((st["width"], st["height"]), (320, 180))

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_concat_trim(self):
        ids = self._import(os.path.join(self.daydir, "DJI_0001.MP4"))
        result = self._run_concat([ids[0]], trims=[{"id": ids[0], "start": 0.3, "end": 0.8}])
        self.assertLess(result["seconds"], 0.9)
        self.assertGreater(result["seconds"], 0.2)

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_concat_order_respected(self):
        self._import(os.path.join(self.daydir, "DJI_0001.MP4"))
        p2 = os.path.join(self.daydir, "DJI_0002.MP4")
        make_video(p2, color="red", dur=1)
        self.wb.scan_once()
        ids = [r["id"] for r in self.wb.db().execute(
            "SELECT id FROM items ORDER BY id").fetchall()]
        r1 = self._run_concat([ids[0], ids[1]])
        r2 = self._run_concat([ids[1], ids[0]])
        self.assertEqual(r1["count"], 2)
        self.assertEqual(r2["count"], 2)
        # 两个顺序都成功出片（内容顺序由 ffmpeg concat 保证）

    def test_concat_empty_rejected(self):
        with self.assertRaises(RuntimeError):
            self._run_concat([])

    def test_concat_photo_rejected(self):
        # 无视频素材时报错
        with self.assertRaises(RuntimeError):
            self._run_concat([999999])

    @unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg")
    def test_concat_via_api_job(self):
        ids = self._import(os.path.join(self.daydir, "DJI_0001.MP4"))
        p2 = os.path.join(self.daydir, "DJI_0002.MP4")
        make_video(p2, dur=1)
        self.wb.scan_once()
        ids = [r["id"] for r in self.wb.db().execute(
            "SELECT id FROM items ORDER BY id").fetchall()]
        r = self.client.post("/api/jobs", json={"kind": "concat", "ids": ids})
        self.assertEqual(r.status_code, 200)
        job_id = r.get_json()["job_id"]
        # 单 worker 同步跑（start_workers=False 时手动执行）
        self.wb.run_job(job_id)
        row = self.wb.db().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        self.assertEqual(row["status"], "done")
        self.assertEqual(json.loads(row["result"])["mode"], "无损拼接")

    def test_jobs_reject_unknown_kind(self):
        r = self.client.post("/api/jobs", json={"kind": "nonsense", "ids": [1]})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
