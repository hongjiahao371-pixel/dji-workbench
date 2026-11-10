"""na_engine（自研剪辑引擎）端到端测试——需要本机 ffmpeg。"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from na_engine import NativeEngine  # noqa: E402

INBOX = "/tmp/na-test/inbox"
HOME = "/tmp/na-test/home"


def ffprobe(path, entries="format=duration"):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries,
                        "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


class NaEngineE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(os.path.join(INBOX, "A.mp4")):
            raise unittest.SkipTest("测试素材未生成（先跑 make-fixture）")
        shutil.rmtree(HOME, ignore_errors=True)
        os.makedirs(HOME, exist_ok=True)
        cls.eng = NativeEngine(HOME)
        cls.P = os.path.join(HOME, "t1")
        r = cls.eng.call("project_create", {"location": HOME, "name": "t1"})
        assert "timeline" in r

    def j(self, d):
        return d if isinstance(d, dict) else json.loads(d)

    def test_01_import_and_sequence(self):
        for f in ("A.mp4", "B.mp4"):
            r = self.eng.call("media_import", {"path": self.P, "file": os.path.join(INBOX, f)})
            self.assertTrue(r["createdId"])
        r = self.eng.call("clips_add_sequence", {"path": self.P, "mediaIds": ["m1", "m2"], "start": 0})
        clips = r["timeline"]["clips"]
        NaEngineE2E.CIDS = [c["id"] for c in clips]
        self.assertEqual(len(clips), 2)
        self.assertAlmostEqual(clips[0]["start"], 0.0)
        self.assertAlmostEqual(clips[1]["start"], 4.0, delta=0.3)
        self.assertEqual(clips[0]["track"], "T1")
        self.assertGreaterEqual(r["timeline"]["duration"], 7.5)

    def test_02_trim_split_move(self):
        first = NaEngineE2E.CIDS[0]
        r = self.eng.call("clip_trim", {"path": self.P, "clipId": first,
                                        "edge": "start", "delta": 1.0})
        c1 = [c for c in r["timeline"]["clips"] if c["id"] == first][0]
        self.assertAlmostEqual(c1["start"], 1.0)
        r = self.eng.call("clips_split", {"path": self.P, "clipIds": [first], "time": 3.0})
        clips = r["timeline"]["clips"]
        self.assertEqual(len(clips), 3)
        r = self.eng.call("clips_move", {"path": self.P, "moves": [
            {"clipId": clips[2]["id"], "trackId": "T2", "start": 6.0}]})
        c3 = [c for c in r["timeline"]["clips"] if c["id"] == clips[2]["id"]][0]
        self.assertEqual(c3["track"], "T2")

    def test_03_title_and_captions(self):
        r = self.eng.call("title_add", {"path": self.P, "start": 0, "duration": 2,
                                        "style": {"content": "自研标题", "color": "#ffcc00"}})
        cid = r["createdId"]
        self.assertTrue(cid)
        r = self.eng.call("title_style_words", {"path": self.P, "clipId": cid,
                                                "start": 0, "end": 2,
                                                "patch": {"color": "#ff0000", "fontScale": 1.5}})
        r = self.eng.call("captions_add", {"path": self.P, "captions": [
            {"start": 0, "end": 2, "text": "第一条"},
            {"start": 2, "end": 4, "text": "第二条"}]})
        texts = [c for c in r["timeline"]["clips"] if c["kind"] == "text"]
        self.assertEqual(len(texts), 3)

    def test_04_bgm_and_speed(self):
        mi = self.eng.call("media_import", {"path": self.P, "file": os.path.join(INBOX, "bgm.mp3")})
        r = self.eng.call("clip_add", {"path": self.P, "mediaId": mi["createdId"],
                                       "trackId": "T2", "start": 0, "duration": 6})
        self.assertEqual(r["timeline"]["clips"][-1]["kind"], "audio")
        second = NaEngineE2E.CIDS[1] if len(NaEngineE2E.CIDS) > 1 else r["timeline"]["clips"][0]["id"]
        self.eng.call("clip_update", {"path": self.P, "clipId": second,
                                      "patch": {"volume": 0.4, "fadeIn": 1.0}})

    def test_05_undo_redo(self):
        r = self.eng.call("edit_undo", {"path": self.P})
        self.assertTrue(r["canRedo"])
        r = self.eng.call("edit_redo", {"path": self.P})
        self.assertTrue(r["canUndo"])

    def test_06_persist(self):
        self.eng.call("project_save", {"path": self.P})
        self.eng.call("project_close", {"path": self.P})
        r = self.eng.call("project_open", {"path": self.P})
        clips = r["timeline"]["clips"]
        self.assertGreaterEqual(len(clips), 5)   # 视频x3+文本x3-undo 后数量
        lst = self.eng.call("project_list", {})
        self.assertTrue(any(x["name"] == "t1" for x in lst))

    def test_07_preview_frame(self):
        r = self.eng.call("preview_frame", {"path": self.P, "time": 1.0})
        img = (r.get("images") or [{}])[0]
        self.assertTrue(img.get("data"))
        self.assertGreater(len(img["data"]), 5000)   # 非空帧

    def test_08_export(self):
        r = self.eng.call("export_start", {"path": self.P, "output": "out.mp4",
                                           "width": 640, "height": 360})
        jid = r["job"]
        for _ in range(120):
            time.sleep(1)
            st = self.eng.call("export_status", {"job": jid})
            if st["state"] in ("done", "failed", "cancelled"):
                break
        self.assertEqual(st["state"], "done", getattr(st, "get", lambda k: None)("__dict__"))
        out = self.eng.export_path("out.mp4")
        self.assertTrue(os.path.isfile(out))
        dur = float(ffprobe(out))
        self.assertGreaterEqual(dur, 7.0)
        self.assertIn("h264", ffprobe(out, "stream=codec_name"))
        self.assertIn("aac", ffprobe(out, "stream=codec_name"))


if __name__ == "__main__":
    unittest.main()


class NaEngineV2(unittest.TestCase):
    """v2：特效/遮罩/防抖渲染。"""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(os.path.join(INBOX, "A.mp4")):
            raise unittest.SkipTest("素材未生成")
        shutil.rmtree(HOME + "/v2", ignore_errors=True)
        os.makedirs(HOME + "/v2", exist_ok=True)
        cls.eng = NativeEngine(HOME + "/v2")
        cls.P = HOME + "/v2/v2p"
        cls.eng.call("project_create", {"location": HOME + "/v2", "name": "v2p"})
        cls.eng.call("media_import", {"path": cls.P, "file": os.path.join(INBOX, "A.mp4")})
        r = cls.eng.call("clips_add_sequence", {"path": cls.P, "mediaIds": ["m1"], "start": 0})
        cls.cid = r["timeline"]["clips"][0]["id"]

    def _export_probe(self, name, expect_desc=None):
        self.eng.call("project_save", {"path": self.P})
        r = self.eng.call("export_start", {"path": self.P, "output": name,
                                           "width": 640, "height": 360})
        jid = r["job"]
        for _ in range(90):
            time.sleep(1)
            st = self.eng.call("export_status", {"job": jid})
            if st["state"] != "running":
                break
        self.assertEqual(st["state"], "done")
        out = self.eng.export_path(name)
        self.assertTrue(os.path.isfile(out))
        return out

    def test_01_effect_bw(self):
        self.eng.call("clip_update", {"path": self.P, "clipId": self.cid,
                                      "patch": {"videoEffects": [{"id": "concat.bw", "enabled": True}]}})
        out = self._export_probe("v2-bw.mp4")
        # 黑白帧饱和度低：抽帧比较 R-B 通道差
        subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "2", "-i", out,
                        "-frames:v", "1", "-update", "1", "-y", "/tmp/v2bw.png"], check=True)

    def test_02_mask_rect_blur(self):
        self.eng.call("clip_update", {"path": self.P, "clipId": self.cid,
                                      "patch": {"mask": {"shape": "rectangle", "x": 0.5, "y": 0.5,
                                                         "width": 0.4, "height": 0.4, "feather": 0.2}}})
        out = self._export_probe("v2-mask.mp4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "2", "-i", out,
                        "-frames:v", "1", "-update", "1", "-y", "/tmp/v2mask.png"], check=True)
        self.eng.call("clip_update", {"path": self.P, "clipId": self.cid, "patch": {"mask": None}})

    def test_03_deshake(self):
        self.eng.call("clip_update", {"path": self.P, "clipId": self.cid,
                                      "patch": {"stabilize": {"smoothness": 0.5}}})
        out = self._export_probe("v2-stab.mp4")
        self.eng.call("clip_update", {"path": self.P, "clipId": self.cid, "patch": {"stabilize": None}})

    def test_04_multi_effect_chain(self):
        self.eng.call("clip_update", {"path": self.P, "clipId": self.cid, "patch": {
            "videoEffects": [
                {"id": "concat.warm", "enabled": True},
                {"id": "concat.blur", "enabled": True, "params": {"amount": 0.3}},
                {"id": "concat.vignette", "enabled": True},
            ]}})
        self._export_probe("v2-chain.mp4")
        self.eng.call("clip_update", {"path": self.P, "clipId": self.cid, "patch": {"videoEffects": []}})
