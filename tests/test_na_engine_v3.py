"""v3：转场 xfade + 关键帧动画端到端。"""
import os
import shutil
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from na_engine import NativeEngine  # noqa: E402

INBOX = "/tmp/na-test/inbox"
HOME = "/tmp/na-test/v3"


class TransitionsKF(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(os.path.join(INBOX, "A.mp4")):
            raise unittest.SkipTest("素材未生成")
        shutil.rmtree(HOME, ignore_errors=True)
        os.makedirs(HOME, exist_ok=True)
        cls.eng = NativeEngine(HOME)

    def _mk(self, name, trans, kf=False):
        P = HOME + "/" + name
        self.eng.call("project_create", {"location": HOME, "name": name})
        for f in ("A.mp4", "B.mp4"):
            self.eng.call("media_import", {"path": P, "file": os.path.join(INBOX, f)})
        d = self.eng.call("clips_add_sequence", {"path": P, "mediaIds": ["m1", "m2"], "start": 0})
        clips = d["timeline"]["clips"]
        self.eng.call("clips_move", {"path": P, "moves": [
            {"clipId": clips[1]["id"], "trackId": clips[0]["track"], "start": clips[0]["duration"]}]})
        self.eng.call("clip_update", {"path": P, "clipId": clips[1]["id"],
                                      "patch": {"transitionIn": {"id": trans, "duration": 1.0}}})
        if kf:
            self.eng.call("clip_update", {"path": P, "clipId": clips[0]["id"], "patch": {"keyframes": [
                {"at": 0, "prop": "scale", "value": 1.0},
                {"at": 1, "prop": "scale", "value": 1.4},
                {"at": 0, "prop": "opacity", "value": 0.2},
                {"at": 0.5, "prop": "opacity", "value": 1.0}]}})
        self.eng.call("project_save", {"path": P})
        r = self.eng.call("export_start", {"path": P, "output": name + ".mp4",
                                           "width": 640, "height": 360})
        for _ in range(90):
            time.sleep(1)
            st = self.eng.call("export_status", {"job": r["job"]})
            if st["state"] != "running":
                break
        self.assertEqual(st["state"], "done", name)
        out = self.eng.export_path(name + ".mp4")
        self.assertTrue(os.path.isfile(out))
        return out

    def test_01_dissolve(self):
        out = self._mk("tr-dissolve", "concat.dissolve")
        self.assertGreater(os.path.getsize(out), 10000)

    def test_02_push(self):
        out = self._mk("tr-push", "concat.push")
        self.assertGreater(os.path.getsize(out), 10000)

    def test_03_wipe(self):
        out = self._mk("tr-wipe", "concat.linear-wipe")
        self.assertGreater(os.path.getsize(out), 10000)

    def test_04_zoom(self):
        out = self._mk("tr-zoom", "concat.zoom-transition")
        self.assertGreater(os.path.getsize(out), 10000)

    def test_05_keyframes_scale_opacity(self):
        out = self._mk("kf-test", "concat.dissolve", kf=True)
        self.assertGreater(os.path.getsize(out), 10000)
        # 抽帧确认前段缩放动画（0.2s vs 2s 画面缩放不同）
        for t, f in ((0.2, "/tmp/kf-1.png"), (2.0, "/tmp/kf-2.png")):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", out,
                            "-frames:v", "1", "-update", "1", "-y", f], check=True)
        s1 = os.path.getsize("/tmp/kf-1.png")
        s2 = os.path.getsize("/tmp/kf-2.png")
        self.assertNotEqual(s1, s2, "缩放动画两帧应不同")


if __name__ == "__main__":
    unittest.main()
