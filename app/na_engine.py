"""na_engine —— DJI 素材工作台自研剪辑引擎（native backend）。

设计目标：不依赖 concat 运行时，用 ffmpeg 内核实现同款剪辑能力。
对外暴露与 concat MCP 工具同形的调用接口 call(tool, args)，
响应形状与 concat 的 timeline_read / project_get 对齐——
前端编辑台（时间轴拖拽/详情面板/全部控件）零改动即可工作。

能力（v1）：
- 工程：create/open/save/close/list、set_video（画幅帧率）
- 片段：media_import、clips_add_sequence、clip_add、clip_trim、clips_split、
        clips_move（跨轨）、clips_remove、clip_update（基础/变换/转场 patch）
- 文本：title_add、captions_add、title_style_words（字符范围着色→分段 drawtext）
- 音频：片段 volume/fadeIn/fadeOut/muted；BGM=clip_add(audio)
- undo/redo（操作栈）
- 渲染：preview_frame（ffmpeg 单帧，快）、export_start/status/cancel（后台线程 job）
- 轨道：track_add（上/下）
"""
import copy
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid


_HAS_DRAWTEXT = None
_AUDIO_CACHE = {}


def _has_audio(path):
    """该文件是否有音轨（缓存；合成测试源常无音轨，盲挂 [i:a] 会炸整图）。"""
    if path not in _AUDIO_CACHE:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=20)
            _AUDIO_CACHE[path] = bool(r.stdout.strip())
        except Exception:
            _AUDIO_CACHE[path] = False
    return _AUDIO_CACHE[path]


_CJK_FONT = None


def _cjk_font():
    """探测 CJK 字体文件（drawtext 中文必需）。"""
    global _CJK_FONT
    if _CJK_FONT is None:
        for cand in ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                     "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                     "/System/Library/Fonts/PingFang.ttc",
                     "/System/Library/Fonts/STHeiti Light.ttc"):
            if os.path.isfile(cand):
                _CJK_FONT = cand
                break
        else:
            _CJK_FONT = ""
    return _CJK_FONT


def _has_drawtext():
    """homebrew ffmpeg 可能不带 libfreetype；NAS 容器内为全功能。缺则跳过文本滤镜。"""
    global _HAS_DRAWTEXT
    if _HAS_DRAWTEXT is None:
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                               capture_output=True, text=True, timeout=15)
            _HAS_DRAWTEXT = " drawtext " in (" " + r.stdout + " ").replace("\n", " ") or "drawtext" in r.stdout
        except Exception:
            _HAS_DRAWTEXT = False
    return _HAS_DRAWTEXT


def _ffprobe_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30)
        return max(0.0, float(r.stdout.strip()))
    except Exception:
        return 0.0


def _ffprobe_kind(path):
    """视频/音频/图片三类。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width", "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        info = json.loads(r.stdout or "{}")
        for st in info.get("streams", []):
            if st.get("codec_type") == "video":
                # 连续视频 vs 静图：有 width 且不是 mjpeg 单帧——用 nb_frames 不可靠，
                # 图片扩展名优先判定
                if os.path.splitext(path)[1].lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                    return "image", None
                return "video", st.get("width")
        for st in info.get("streams", []):
            if st.get("codec_type") == "audio":
                return "audio", None
    except Exception:
        pass
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        return "image", None
    if ext in (".mp3", ".wav", ".m4a", ".aac", ".flac"):
        return "audio", None
    return "video", None


class _Job:
    def __init__(self, jid, output):
        self.id = jid
        self.output = output
        self.proc = None
        self.state = "running"   # running|done|failed|cancelled
        self.total = 0
        self.frame = 0
        self.started = time.time()

    def status(self, w, h):
        frac = min(1.0, self.frame / self.total) if self.total else 0.0
        return {"job": self.id, "state": self.state, "fraction": round(frac, 4),
                "frame": self.frame, "total": self.total, "width": w, "height": h,
                "output": self.output, "stage": "rendering"}


class NativeEngine:
    """每个工作台进程一个实例；多工程打开状态在 self.projects。"""

    def __init__(self, home):
        self.home = home                 # 工程根（如 /data/na-projects）
        self.exports = os.path.join(home, "exports")
        os.makedirs(self.exports, exist_ok=True)
        self.projects = {}               # path -> project dict（打开状态）
        self.undo = {}                   # path -> [snapshots]
        self.redo = {}
        self.jobs = {}                   # jid -> _Job
        self._jid = 0
        self._lock = threading.RLock()

    # ---------- 工具协议入口 ----------
    def call(self, tool, args):
        with self._lock:
            fn = getattr(self, "_t_" + tool, None)
            if fn is None:
                raise RuntimeError(f"native 引擎暂不支持 {tool}")
            return fn(args or {})

    # ---------- 工程生命周期 ----------
    def _proj_path(self, name_or_loc, maybe_name=None):
        if maybe_name:
            return os.path.join(name_or_loc, maybe_name)
        return name_or_loc

    def _new_project(self, name, w=1920, h=1080, fps=30):
        return {
            "name": name, "width": w, "height": h, "fps": fps,
            "media": [],                 # [{id,kind,name,path,duration,width}]
            "tracks": [
                {"id": "T1", "clips": []},
                {"id": "T2", "clips": []},
            ],
            "duration": 0.0,
        }

    def _t_project_create(self, a):
        loc = a.get("location") or self.home
        name = a.get("name") or ("wb-" + str(int(time.time() * 1000)))
        video = a.get("video") or {}
        p = self._new_project(
            name,
            int(video.get("width", 1920)), int(video.get("height", 1080)),
            int(video.get("rateNum") or video.get("fps") or 30))
        path = os.path.join(loc, name)
        os.makedirs(path, exist_ok=True)
        self.projects[path] = p
        self._save_file(path)
        return self._doc(path)

    def _t_project_open(self, a):
        path = a["path"]
        f = os.path.join(path, "project.json")
        if not os.path.isfile(f):
            raise RuntimeError(f"工程不存在: {path}")
        with open(f, encoding="utf-8") as fh:
            self.projects[path] = json.load(fh)
        return self._doc(path)

    def _t_project_list(self, a):
        return self._project_list_impl(a)

    def _project_list_impl(self, a):
        out = []
        if os.path.isdir(self.home):
            for n in sorted(os.listdir(self.home)):
                f = os.path.join(self.home, n, "project.json")
                if os.path.isfile(f):
                    try:
                        with open(f, encoding="utf-8") as fh:
                            d = json.load(fh)
                        out.append({"name": n, "path": os.path.join(self.home, n),
                                    "width": d.get("width"), "height": d.get("height"),
                                    "rateNum": d.get("fps"), "rateDen": 1})
                    except Exception:
                        pass
        return out

    def _save_file(self, path):
        p = self.projects.get(path)
        if p is None:
            return
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "project.json"), "w", encoding="utf-8") as fh:
            json.dump(p, fh, ensure_ascii=False, indent=1)

    def _t_project_save(self, a):
        self._save_file(a["path"])
        return {}

    def _t_project_close(self, a):
        if a.get("save", True):
            self._save_file(a["path"])
        self.projects.pop(a["path"], None)
        return {}

    def _t_project_set_video(self, a):
        p = self._proj(a["path"])
        v = a["video"]
        if "width" in v: p["width"] = int(v["width"])
        if "height" in v: p["height"] = int(v["height"])
        if "rateNum" in v: p["fps"] = int(v["rateNum"])
        elif "fps" in v: p["fps"] = int(v["fps"])
        return self._doc(a["path"])

    def _proj(self, path):
        p = self.projects.get(path)
        if p is None:
            raise RuntimeError("工程未打开（先 project_open）")
        return p

    # ---------- 媒体 ----------
    def _t_media_import(self, a):
        p = self._proj(a["path"])
        f = a["file"]
        kind, w = _ffprobe_kind(f)
        dur = _ffprobe_duration(f) if kind in ("video", "audio") else 5.0
        mid = "m" + str(len(p["media"]) + 1)
        p["media"].append({"id": mid, "kind": kind, "name": os.path.basename(f),
                           "path": f, "duration": round(dur, 3), "width": w})
        return {"createdId": mid, "media": p["media"],
                "project": self._pinfo(a["path"]), "timeline": self._tlinfo(a["path"]),
                "canUndo": True, "canRedo": False}

    # ---------- 片段 ----------
    def _snap(self, path, p):
        self.undo.setdefault(path, []).append(copy.deepcopy(p))
        self.redo.setdefault(path, [])[:] = []

    def _recalc(self, p):
        end = 0.0
        for t in p["tracks"]:
            for c in t["clips"]:
                end = max(end, float(c.get("start", 0)) + float(c.get("duration", 0)))
        p["duration"] = round(end, 3)

    def _clip(self, p, cid):
        for t in p["tracks"]:
            for c in t["clips"]:
                if c["id"] == cid:
                    return c, t
        raise RuntimeError("片段不存在: " + cid)

    def _t_clips_add_sequence(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        start = float(a.get("start", 0))
        for mid in a.get("mediaIds", []):
            m = next((x for x in p["media"] if x["id"] == mid), None)
            if not m:
                continue
            cid = "c" + str(uuid.uuid4().hex[:6])
            p["tracks"][0]["clips"].append({
                "id": cid, "kind": m["kind"], "name": m["name"], "mediaId": mid,
                "path": m["path"], "start": start, "duration": m["duration"],
                "sourceStart": 0.0, "speed": 1.0, "volume": 1.0,
                "fadeIn": 0.0, "fadeOut": 0.0, "muted": False,
            })
            start += m["duration"]
        self._recalc(p)
        return self._doc(path)

    def _t_clip_add(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        m = next((x for x in p["media"] if x["id"] == a["mediaId"]), None)
        if not m:
            raise RuntimeError("媒体不存在")
        tid = a.get("trackId")
        track = next((t for t in p["tracks"] if t["id"] == tid), p["tracks"][0])
        cid = "c" + str(uuid.uuid4().hex[:6])
        dur = a.get("duration") or m["duration"]
        track["clips"].append({
            "id": cid, "kind": m["kind"], "name": m["name"], "mediaId": m["id"],
            "path": m["path"], "start": float(a.get("start", 0)),
            "duration": float(dur), "sourceStart": 0.0, "speed": 1.0,
            "volume": 1.0, "fadeIn": 0.0, "fadeOut": 0.0, "muted": False,
        })
        self._recalc(p)
        return self._doc(path)

    def _t_clip_trim(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        c, _ = self._clip(p, a["clipId"])
        d = float(a.get("delta", 0))
        if a.get("edge") == "start":
            c["sourceStart"] = round(float(c.get("sourceStart", 0)) + d, 3)
            c["start"] = round(float(c.get("start", 0)) + d, 3)
            c["duration"] = round(max(0.1, float(c["duration"]) - d), 3)
        else:
            c["duration"] = round(max(0.1, float(c["duration"]) + d), 3)
        self._recalc(p)
        return self._doc(path)

    def _t_clips_split(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        t = float(a["time"])
        out = []
        for cid in a.get("clipIds", []):
            c, tr = self._clip(p, cid)
            rel = t - float(c["start"])
            if rel <= 0.1 or rel >= float(c["duration"]) - 0.1:
                continue
            left_dur = round(rel, 3)
            right = copy.deepcopy(c)
            right["id"] = "c" + str(uuid.uuid4().hex[:6])
            right["start"] = round(float(c["start"]) + rel, 3)
            right["duration"] = round(float(c["duration"]) - rel, 3)
            right["sourceStart"] = round(float(c.get("sourceStart", 0)) + rel, 3)
            c["duration"] = left_dur
            if c.get("fadeOut"):
                c["fadeOut"] = 0.0
            if right.get("fadeIn"):
                right["fadeIn"] = 0.0
            tr["clips"].append(right)
            out.append(right["id"])
        self._recalc(p)
        d = self._doc(path)
        d["createdId"] = out[0] if out else None
        return d

    def _t_clips_move(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        for mv in a.get("moves", []):
            c, tr = self._clip(p, mv["clipId"])
            tid = mv.get("trackId")
            if tid:
                target = next((t for t in p["tracks"] if t["id"] == tid), None)
                if target is not None and target is not tr:
                    tr["clips"].remove(c)
                    target["clips"].append(c)
            c["start"] = max(0.0, round(float(mv.get("start", c["start"])), 3))
        self._recalc(p)
        return self._doc(path)

    def _t_clips_remove(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        for cid in a.get("clipIds", []):
            c, tr = self._clip(p, cid)
            tr["clips"].remove(c)
        self._recalc(p)
        return self._doc(path)

    _PATCH_OK = {"volume", "fadeIn", "fadeOut", "muted", "speed", "name",
                 "scale", "offsetX", "offsetY", "rotation", "opacity",
                 "flipH", "flipV", "text", "transitionIn"}

    def _t_clip_update(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        c, _ = self._clip(p, a["clipId"])
        patch = a.get("patch") or {}
        if "text" in patch and isinstance(patch["text"], dict):
            tx = c.setdefault("text", {"content": "", "fontSize": 64, "color": "#ffffff",
                                       "x": 0.5, "y": 0.5, "align": "center", "bold": False})
            for k, v in patch["text"].items():
                tx[k] = v
        if "transitionIn" in patch:
            c["transitionIn"] = patch["transitionIn"]
        if "videoEffects" in patch:
            c["videoEffects"] = patch["videoEffects"]
        if "mask" in patch:
            c["mask"] = patch["mask"]
        if "keyframes" in patch:
            c["keyframes"] = patch["keyframes"]
        if "speed" in patch:
            c["speed"] = float(patch["speed"])
        for k in ("volume", "fadeIn", "fadeOut", "muted", "name", "scale",
                  "offsetX", "offsetY", "rotation", "opacity", "flipH", "flipV"):
            if k in patch:
                c[k] = patch[k]
        self._recalc(p)
        return self._doc(path)

    def _t_clip_speed(self, a):
        return self._t_clip_update({"path": a["path"], "clipId": a["clipId"],
                                    "patch": {"speed": float(a.get("speed", 1))}})

    # ---------- 文本 ----------
    def _t_title_add(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        style = a.get("style") or {}
        cid = "c" + str(uuid.uuid4().hex[:6])
        above = a.get("above", True)
        track = p["tracks"][1] if len(p["tracks"]) > 1 else p["tracks"][0]
        track["clips"].append({
            "id": cid, "kind": "text", "name": (style.get("content") or "标题")[:12],
            "start": float(a.get("start", 0)),
            "duration": float(a.get("duration") or 3.0),
            "text": {"content": style.get("content", "标题"),
                     "fontSize": style.get("fontSize", 64) if isinstance(style.get("fontSize"), (int, float)) and style.get("fontSize", 64) > 1 else int((style.get("fontSize", 0.09) or 0.09) * p["height"]),
                     "color": style.get("color", "#ffffff"),
                     "x": 0.5, "y": 0.5, "align": style.get("align", "center"),
                     "bold": style.get("fontWeight", 400) >= 700},
            "runs": [],        # 富文本词样式 [{start,end,color,fontScale,fontWeight}]
        })
        self._recalc(p)
        d = self._doc(path)
        d["createdId"] = cid
        return d

    def _t_captions_add(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        track = p["tracks"][1] if len(p["tracks"]) > 1 else p["tracks"][0]
        created = None
        for cap in a.get("captions", []):
            cid = "c" + str(uuid.uuid4().hex[:6])
            st = a.get("style") or {}
            track["clips"].append({
                "id": cid, "kind": "text", "name": cap.get("text", "")[:12],
                "start": float(cap.get("start", 0)),
                "duration": round(float(cap.get("end", 2)) - float(cap.get("start", 0)), 3),
                "caption": True,
                "text": {"content": cap.get("text", ""),
                         "fontSize": 48, "color": st.get("color", "#ffffff"),
                         "x": 0.5, "y": 0.9, "align": "center"},
                "runs": [],
            })
            created = created or cid
        self._recalc(p)
        d = self._doc(path)
        d["createdId"] = created
        return d

    def _t_title_style_words(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        c, _ = self._clip(p, a["clipId"])
        runs = c.setdefault("runs", [])
        runs.append({"start": int(a.get("start", 0)), "end": int(a.get("end", 0)),
                     **{k: v for k, v in (a.get("patch") or {}).items()}})
        return self._doc(path)

    def _t_title_clear_word_styles(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        c, _ = self._clip(p, a["clipId"])
        s, e = a.get("start"), a.get("end")
        if s is None and e is None:
            c["runs"] = []
        else:
            c["runs"] = [r for r in c.get("runs", [])
                         if r["end"] <= (s or 0) or r["start"] >= (e or 10 ** 9)]
        return self._doc(path)

    # ---------- 轨道 ----------
    def _t_track_add(self, a):
        path = a["path"]
        p = self._proj(path)
        self._snap(path, p)
        n = len(p["tracks"]) + 1
        tid = "T" + str(n)
        t = {"id": tid, "clips": []}
        if a.get("bottom"):
            p["tracks"].insert(0, t)     # tracks[0]=主轨（最下）
        else:
            p["tracks"].append(t)
        return self._doc(path)

    # ---------- undo/redo ----------
    def _t_edit_undo(self, a):
        path = a["path"]
        st = self.undo.get(path) or []
        if not st:
            raise RuntimeError("没有可撤销的操作")
        p = self._proj(path)
        self.redo.setdefault(path, []).append(copy.deepcopy(p))
        self.projects[path] = st.pop()
        self._save_file(path)
        return self._doc(path)

    def _t_edit_redo(self, a):
        path = a["path"]
        st = self.redo.get(path) or []
        if not st:
            raise RuntimeError("没有可重做的操作")
        p = self._proj(path)
        self.undo.setdefault(path, []).append(copy.deepcopy(p))
        self.projects[path] = st.pop()
        self._save_file(path)
        return self._doc(path)

    # ---------- timeline_read 兼容形状 ----------
    def _tlinfo(self, path):
        p = self.projects[path]
        clips = []
        for t in p["tracks"]:
            for c in t["clips"]:
                clips.append({
                    "id": c["id"], "kind": c["kind"], "name": c.get("name", c["id"]),
                    "start": float(c.get("start", 0)), "duration": float(c.get("duration", 0)),
                    "end": round(float(c.get("start", 0)) + float(c.get("duration", 0)), 3),
                    "track": t["id"],
                    "sourceStart": float(c.get("sourceStart", 0)),
                    "speed": float(c.get("speed", 1)),
                    "volume": float(c.get("volume", 1)),
                    "fadeIn": float(c.get("fadeIn", 0)), "fadeOut": float(c.get("fadeOut", 0)),
                    "muted": bool(c.get("muted", False)),
                    "scale": float(c.get("scale", 1)), "offsetX": float(c.get("offsetX", 0)),
                    "offsetY": float(c.get("offsetY", 0)), "rotation": float(c.get("rotation", 0)),
                    "opacity": float(c.get("opacity", 1)),
                    "flipH": bool(c.get("flipH", False)), "flipV": bool(c.get("flipV", False)),
                    "text": c.get("text"), "transitionIn": c.get("transitionIn"),
                    "videoEffects": c.get("videoEffects", []),
                    "mask": c.get("mask"), "keyframes": c.get("keyframes", []),
                })
        return {"id": "TL1", "name": p["name"], "duration": p["duration"],
                "clips": clips,
                "tracks": [{"id": t["id"], "clips": [c["id"] for c in t["clips"]]}
                           for t in p["tracks"]],
                "media": p["media"]}

    def _pinfo(self, path):
        p = self.projects[path]
        return {"name": p["name"], "fps": float(p["fps"]),
                "width": p["width"], "height": p["height"],
                "timelines": [self._tlinfo(path)]}

    def _doc(self, path):
        return {"canUndo": bool(self.undo.get(path)),
                "canRedo": bool(self.redo.get(path)),
                "project": self._pinfo(path),
                "timeline": self._tlinfo(path),
                "media": self.projects[path]["media"]}

    def _t_timeline_read(self, a):
        if a["path"] not in self.projects:
            try: self._t_project_open({"path": a["path"]})
            except Exception: pass
        return self._doc(a["path"])

    def _t_project_get(self, a):
        return self._doc(a["path"])

    def _t_concat_version(self, a):
        return {"version": "native-1.0", "mode": "self-built"}

    _EFFECTS = [
        {"id": "concat.bw", "name": "黑白", "kind": "effect", "params": []},
        {"id": "concat.sepia", "name": "复古", "kind": "effect", "params": []},
        {"id": "concat.warm", "name": "暖色", "kind": "effect", "params": []},
        {"id": "concat.cool", "name": "冷色", "kind": "effect", "params": []},
        {"id": "concat.vignette", "name": "暗角", "kind": "effect", "params": []},
        {"id": "concat.blur", "name": "模糊", "kind": "effect",
         "params": [{"key": "amount", "type": "float", "default": 0.5, "step": 0.1}]},
        {"id": "concat.sharpen", "name": "锐化", "kind": "effect",
         "params": [{"key": "amount", "type": "float", "default": 0.5, "step": 0.1}]},
    ]

    def _t_effects_list(self, a):
        return self._EFFECTS

    # ---------- 渲染：ffmpeg 滤镜图 ----------
    def _esc_text(self, s):
        return (str(s).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
                .replace("%", "\\%").replace(",", "\\,").replace("[", "\\[").replace("]", "\\]"))

    def _hex_to_rgb(self, hx):
        hx = (hx or "#ffffff").lstrip("#")
        if len(hx) == 3:
            hx = "".join(ch * 2 for ch in hx)
        try:
            return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return (255, 255, 255)

    def _build_graph(self, p, with_audio=True):
        """构建 (inputs, filter_complex, out_v, out_a, total_frames)。

        v1 语义：
        - 主轨（tracks[0]）视频/图片 = 底层，按 start 排序铺在黑底上（空隙=黑）
        - 其余轨视频/图片依次向上 overlay（透明背景用 null 源则跳过）
        - text 片段 → drawtext 逐条叠时间窗（含 runs 富文本分段）
        - audio 片段与视频自带音轨 → volume/afade/atempo → adelay → amix
        """
        W, H, FPS = p["width"], p["height"], float(p["fps"])
        dur = max(0.5, p["duration"] + 0.1)
        inputs = ["-f", "lavfi", "-t", f"{dur:.3f}",
                  "-i", f"color=c=black:s={W}x{H}:r={FPS}"]      # 输入0=底
        parts = []
        base = "[0:v]"
        vi = 1
        video_lanes = []      # [(stream_label, lane_index)]
        audio_streams = []    # [(label)]
        lane = 0
        for ti, tr in enumerate(p["tracks"]):
            vclips = [c for c in tr["clips"]
                      if c.get("kind") in ("video", "image") and not c.get("muted_only")]
            if not vclips:
                continue
            lane += 1
            seg_labels = []
            for c in vclips:
                m = next((x for x in p["media"] if x["id"] == c.get("mediaId")), None)
                src = c.get("path") or (m or {}).get("path")
                if not src:
                    continue
                speed = max(0.0625, min(16.0, float(c.get("speed", 1))))
                want = float(c["duration"])
                seg_dur = max(0.04, want * speed)      # 源内时长
                if c.get("kind") == "image":
                    inputs += ["-loop", "1", "-t", f"{seg_dur:.3f}", "-r", str(FPS), "-i", src]
                else:
                    inputs += ["-ss", f"{float(c.get('sourceStart', 0)):.3f}",
                               "-t", f"{seg_dur:.3f}", "-i", src]
                lab = f"v{vi}"
                vi += 1
                # 片段处理链：适配画幅 + 变换 + 变速 + 定位到 start
                sc = float(c.get("scale", 1))
                rot = float(c.get("rotation", 0))
                fx = []
                fx.append(f"scale={max(2, int(W * sc))}:{max(2, int(H * sc))}:force_original_aspect_ratio=decrease")
                fx.append(f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0")
                if c.get("flipH"): fx.append("hflip")
                if c.get("flipV"): fx.append("vflip")
                if abs(rot) > 0.5:
                    fx.append(f"rotate={rot}*PI/180:fillcolor=none")
                ox = float(c.get("offsetX", 0)); oy = float(c.get("offsetY", 0))
                if abs(ox) > 0.001 or abs(oy) > 0.001:
                    fx.append(f"pad=iw+{W}:ih+{H}:(iw+{W})/2-(iw/2)+{int(ox*W)}:(ih+{H})/2-(ih/2)+{int(oy*H)}:color=black@0")
                    fx.append(f"crop={W}:{H}:{W}/2:{H}/2")
                op = float(c.get("opacity", 1))
                # —— 特效滤镜（videoEffects，0.2.9 兼容 id）——
                for eff in (c.get("videoEffects") or []):
                    if eff.get("enabled") is False:
                        continue
                    eid = eff.get("id", "")
                    pr = eff.get("params") or {}
                    if eid in ("concat.bw", "concat.black-white"):
                        fx.append("hue=s=0")
                    elif eid == "concat.sepia":
                        fx.append("colorbalance=rs=.3:gs=.05:bs=-.15,colorchannelmixer=.393:.769:.189:.349:.686:.168:.272:.534:.131")
                    elif eid == "concat.warm":
                        fx.append("colorbalance=rs=.12:bs=-.12")
                    elif eid == "concat.cool":
                        fx.append("colorbalance=rs=-.12:bs=.12")
                    elif eid in ("concat.vignette",):
                        fx.append("vignette=PI/4")
                    elif eid in ("concat.blur", "concat.gaussian"):
                        amt = max(1, min(30, int(float(pr.get("amount", 0.5)) * 40)))
                        fx.append(f"gblur=sigma={amt}")
                    elif eid == "concat.sharpen":
                        amt = 0.5 + float(pr.get("amount", 0.5)) * 2
                        fx.append(f"unsharp=5:5:{amt:.2f}")
                    elif eid == "concat.brighten":
                        fx.append(f"eq=brightness={max(-1, min(1, float(pr.get('amount', 0.3)))):.2f}")
                    elif eid == "concat.contrast":
                        fx.append(f"eq=contrast={1 + max(-0.9, min(2, float(pr.get('amount', 0.3)))):.2f}")
                # —— 防抖（线性 deshake 可入链）——
                if c.get("stabilize"):
                    fx.append("deshake=rx=24:ry=24")
                op = float(c.get("opacity", 1))
                tr_in = c.get("transitionIn") or {}
                if op < 0.999: fx.append(f"format=rgba,colorchannelmixer=aa={max(0, min(1, op)):.3f},format=rgb0")
                chain = ",".join(fx)
                if c.get("kind") == "video":
                    chain += f",setpts=PTS/{speed}"
                else:
                    chain += f",fps={FPS}"
                # 淡入淡出在 setpts（时间轴域）之后：按时间轴时长 want 计，不受变速/修剪影响
                want = float(c["duration"])
                fades = []
                fin = float(c.get("fadeIn", 0)); fout = float(c.get("fadeOut", 0))
                if fin > 0.01: fades.append(f"fade=t=in:st=0:d={min(fin, want):.2f}")
                if fout > 0.01: fades.append(f"fade=t=out:st={max(0, want - fout):.2f}:d={min(fout, want):.2f}")
                if tr_in.get("id") in ("cross-fade", "fade"):
                    d = min(float(tr_in.get("duration", 0.5)), want)
                    if d > 0.01: fades.append(f"fade=t=in:st=0:d={d:.2f}")
                if fades:
                    chain += "," + ",".join(fades)
                seg_out = f"s{lab}"
                parts.append(f"[{vi - 1}:v]{chain}[{seg_out}];")
                # —— 遮罩：区域打码/模糊（split 非线性结构）——
                mask = c.get("mask")
                if mask and mask.get("shape") in ("rectangle", "circle") and not mask.get("invert"):
                    mw = max(0.05, min(1.0, float(mask.get("width", 0.5))))
                    mh = max(0.05, min(1.0, float(mask.get("height", 0.5))))
                    mx = max(0, min(1 - mw, float(mask.get("x", 0.5)) - mw / 2))
                    my = max(0, min(1 - mh, float(mask.get("y", 0.5)) - mh / 2))
                    cw = max(2, int(W * mw) // 2 * 2)
                    ch = max(2, int(H * mh) // 2 * 2)
                    cx = max(0, int(W * mx) // 2 * 2)
                    cy = max(0, int(H * my) // 2 * 2)
                    sigma = max(2, int(20 * (1 - float(mask.get("feather", 0.5)))))
                    sp = f"{seg_out}_split"
                    bl = f"{seg_out}_blur"
                    ov = f"{seg_out}_m"
                    parts.append(f"[{seg_out}]split=2[{sp}][{seg_out}b];")
                    parts.append(f"[{seg_out}b]crop={cw}:{ch}:{cx}:{cy},gblur=sigma={sigma}[{bl}];")
                    parts.append(f"[{sp}][{bl}]overlay={cx}:{cy}:enable='between(t,{float(c['start']):.3f},{float(c['start'])+want:.3f})'[{seg_out}_m];")
                    seg_out = ov
                elif mask and mask.get("shape") in ("rectangle", "circle") and mask.get("invert"):
                    # 反选：主画面模糊，原区域清晰覆盖回
                    mw = max(0.05, min(1.0, float(mask.get("width", 0.5))))
                    mh = max(0.05, min(1.0, float(mask.get("height", 0.5))))
                    mx = max(0, min(1 - mw, float(mask.get("x", 0.5)) - mw / 2))
                    my = max(0, min(1 - mh, float(mask.get("y", 0.5)) - mh / 2))
                    cw = max(2, int(W * mw) // 2 * 2)
                    ch = max(2, int(H * mh) // 2 * 2)
                    cx = max(0, int(W * mx) // 2 * 2)
                    cy = max(0, int(H * my) // 2 * 2)
                    sp = f"{seg_out}_split"
                    cl = f"{seg_out}_crop"
                    bl = f"{seg_out}_blur"
                    ov = f"{seg_out}_m"
                    parts.append(f"[{seg_out}]split=2[{sp}][{cl}];")
                    parts.append(f"[{sp}]gblur=sigma=18[{bl}];")
                    parts.append(f"[{bl}][{cl}]overlay={cx}:{cy}:enable='between(t,{float(c['start']):.3f},{float(c['start'])+want:.3f})'[{seg_out}_m];")
                    seg_out = ov
                seg_labels.append((seg_out, float(c["start"]), want))
            # 每轨：黑底 + overlay 每片段于其时间窗
            lane_in = f"l{ti}_in"
            parts.append(f"color=c=black@0:s={W}x{H}:r={FPS}:d={dur:.3f}[{lane_in}];")
            cur = f"[{lane_in}]"
            for k, (seg, st, want) in enumerate(seg_labels):
                nxt = f"l{ti}_{k}"
                parts.append(f"{cur}[{seg}]overlay=x={int(0)}:y={int(0)}"
                             f":enable='between(t,{st:.3f},{st + want:.3f})':format=auto[{nxt}];")
                cur = f"[{nxt}]"
            video_lanes.append((cur, lane))
        # 轨间叠加（从底往上）
        for k in range(1, len(video_lanes)):
            prev = video_lanes[k - 1][0]
            cur = video_lanes[k][0]
            outl = f"ov{k}"
            parts.append(f"{prev}{cur}overlay=0:0:format=auto[{outl}];")
            video_lanes[k] = (f"[{outl}]", video_lanes[k][1])
        vsrc = video_lanes[-1][0] if video_lanes else base
        # 文本（ffmpeg 无 drawtext 时自动跳过——如 homebrew 构建缺 libfreetype）
        text_clips = []
        if _has_drawtext():
            for tr in p["tracks"]:
                for c in tr["clips"]:
                    if c.get("kind") == "text" and (c.get("text") or {}).get("content"):
                        text_clips.append(c)
        for c in text_clips:
            if True:
                tx = c.get("text") or {}
                content = tx.get("content", "")
                st = float(c["start"]); en = st + float(c["duration"])
                size = int(float(tx.get("fontSize", 48)))
                col = "0x%02X%02X%02X" % self._hex_to_rgb(tx.get("color"))
                bold = 1 if tx.get("bold") else 0
                x = f"(w-text_w)*{float(tx.get('x', 0.5)):.3f}"
                y = f"(h-text_h)*{float(tx.get('y', 0.5)):.3f}"
                pieces = [(0, len(content), None)]
                runs = c.get("runs") or []
                if runs:
                    pieces = []
                    pos = 0
                    for r in sorted(runs, key=lambda r: r.get("start", 0)):
                        s0, e0 = int(r.get("start", 0)), int(r.get("end", 0))
                        if s0 > pos:
                            pieces.append((pos, s0, None))
                        pieces.append((s0, min(e0, len(content)), r))
                        pos = max(pos, e0)
                    if pos < len(content):
                        pieces.append((pos, len(content), None))
                for (s0, e0, run) in pieces:
                    if e0 <= s0:
                        continue
                    seg_txt = content[s0:e0]
                    rcol = col; rsize = size; rbold = bold
                    if run:
                        rcol = "0x%02X%02X%02X" % self._hex_to_rgb(run.get("color"))
                        rsize = int(size * float(run.get("fontScale", 1)))
                        rbold = 1 if (run.get("fontWeight", 400) or 400) >= 700 else bold
                    boldw = ":borderw=2:bordercolor=black@0.85" if rbold else ""
                    ff = _cjk_font()
                    dt = (f"drawtext=text='{self._esc_text(seg_txt)}'"
                          f"{':fontfile=' + ff if ff else ''}"
                          f":fontsize={rsize}:fontcolor={rcol}{boldw}"
                          f":x='{x}':y='{y}'"
                          f":enable='between(t,{st:.3f},{en:.3f})'")
                    nxt = f"txt{len(parts)}"
                    parts.append(f"{vsrc}{dt}[{nxt}];")
                    vsrc = f"[{nxt}]"
        # 音频
        ai_total = vi
        for tr in p["tracks"] if with_audio else []:
            for c in tr["clips"]:
                m = next((x for x in p["media"] if x["id"] == c.get("mediaId")), None)
                src = c.get("path") or (m or {}).get("path")
                if not src:
                    continue
                kind = c.get("kind")
                if kind not in ("video", "audio"):
                    continue
                if not _has_audio(src):
                    continue          # 无音轨文件（如纯画面测试源）不挂音频输入
                speed = max(0.0625, min(16.0, float(c.get("speed", 1))))
                want = float(c["duration"]); seg_dur = max(0.04, want * speed)
                inputs += ["-ss", f"{float(c.get('sourceStart', 0)):.3f}",
                           "-t", f"{seg_dur:.3f}", "-i", src]
                lab = f"a{ai_total}"
                af = []
                if speed != 1.0:
                    tempo = 1.0 / speed
                    chain_t = []
                    while tempo > 2.0:
                        chain_t.append("atempo=2.0"); tempo /= 2.0
                    while tempo < 0.5:
                        chain_t.append("atempo=0.5"); tempo /= 0.5
                    chain_t.append(f"atempo={tempo:.4f}")
                    af.append(",".join(chain_t))
                vol = 0.0 if c.get("muted") else float(c.get("volume", 1))
                af.append(f"volume={max(0, vol):.3f}")
                fin = float(c.get("fadeIn", 0)); fout = float(c.get("fadeOut", 0))
                if fin > 0.01: af.append(f"afade=t=in:st=0:d={min(fin, seg_dur/2):.2f}")
                if fout > 0.01: af.append(f"afade=t=out:st={max(0, seg_dur - fout):.2f}:d={min(fout, seg_dur/2):.2f}")
                delay_ms = int(float(c.get("start", 0)) * 1000)
                af.append(f"adelay={delay_ms}|{delay_ms}")
                alab = f"am{ai_total}"
                parts.append(f"[{ai_total}:a]{','.join(af)}[{alab}];")
                audio_streams.append(f"[{alab}]")
                ai_total += 1
                if kind == "video":
                    # 视频复用输入去重：视频段已作为视频输入加过——音频这里再挂一次输入
                    pass
        out_a = None
        if audio_streams:
            if len(audio_streams) == 1:
                out_a = audio_streams[0]
            else:
                out_a = "[aout]"
                parts.append("".join(audio_streams) +
                             f"amix=inputs={len(audio_streams)}:duration=longest:dropout_transition=0[aout];")
        total_frames = int(dur * FPS)
        return inputs, parts, vsrc, out_a, total_frames, W, H, FPS

    # ---------- 预览 ----------
    _prev_seq = 0
    _prev_lock = threading.Lock()

    def _t_preview_frame(self, a):
        path = a["path"]
        if path not in self.projects:      # 进程重启后状态丢失：自动恢复
            try:
                self._t_project_open({"path": path})
            except Exception:
                pass
        p = self._proj(path)
        t = max(0.0, float(a.get("time", 0)))
        inputs, parts, vsrc, _oa, _tf, W, H, FPS = self._build_graph(p, with_audio=False)
        with NativeEngine._prev_lock:
            NativeEngine._prev_seq += 1
            png = os.path.join(self.exports, f".preview-{NativeEngine._prev_seq}.png")
        graph = "".join(parts) + f"{vsrc}format=rgb24[vout]"
        cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + inputs +
               ["-filter_complex", graph, "-map", "[vout]",
                "-ss", f"{t:.3f}", "-frames:v", "1", png])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode != 0 or not os.path.isfile(png):
                raise RuntimeError((r.stderr or "预览失败").strip()[-300:])
            with open(png, "rb") as fh:
                data = fh.read()
            return {"images": [{"mime": "image/png", "data": _b64(data)}],
                    "text": json.dumps({"images": [{"mime": "image/png", "data": _b64(data)}]},
                                       ensure_ascii=False)}
        finally:
            try: os.remove(png)
            except Exception: pass

    # ---------- 导出 ----------
    def _t_export_start(self, a):
        path = a["path"]
        p = self._proj(path)
        self._save_file(path)
        out_w = int(a.get("width") or p["width"])
        out_h = int(a.get("height") or p["height"])
        name = os.path.basename(a.get("output") or f"wb-{int(time.time()*1000)}.mp4")
        outp = os.path.join(self.exports, name)
        inputs, parts, vsrc, out_a, tf, W, H, FPS = self._build_graph(p)
        graph = "".join(parts)
        if (out_w, out_h) != (W, H):
            graph += f"{vsrc}scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2[vfinal];"
            vsrc = "[vfinal]"
        graph += f"{vsrc}format=yuv420p[vout];"
        maps = ["-map", "[vout]"]
        if out_a:
            graph += f"{out_a}anull[aout]"
            maps += ["-map", "[aout]"]
        else:
            maps += ["-an"]
        cmd = (["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + inputs +
               ["-filter_complex", graph] + maps +
               ["-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-t", f"{max(0.5, p['duration'] + 0.1):.3f}",
                outp])
        with self._lock:
            self._jid += 1
            jid = "j" + str(self._jid)
            job = _Job(jid, outp)
            job.total = int(tf * out_w / max(1, W)) or tf
            job.total = tf
            self.jobs[jid] = job

        def run():
            job.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, text=True)
            # 从 ffmpeg -stats 不可用（loglevel error）；按时间估算进度
            t0 = time.time()
            est = max(5.0, p["duration"] * 1.5)
            while job.proc.poll() is None:
                time.sleep(0.5)
                job.frame = min(job.total, int((time.time() - t0) / est * job.total))
            if job.proc.returncode == 0 and os.path.isfile(outp):
                job.state = "done"; job.frame = job.total
            elif job.state != "cancelled":
                job.state = "failed"
        threading.Thread(target=run, daemon=True).start()
        return {"job": jid, "output": outp, "path": path}

    def _t_export_status(self, a):
        with self._lock:
            job = self.jobs.get(a["job"])
        if not job:
            raise RuntimeError("任务不存在")
        return job.status(0, 0)

    def _t_export_cancel(self, a):
        with self._lock:
            job = self.jobs.get(a["job"])
        if job and job.proc and job.proc.poll() is None:
            job.proc.terminate()
            job.state = "cancelled"
        return {"job": a["job"], "state": "cancelled"}

    def export_path(self, name):
        return os.path.join(self.exports, os.path.basename(name))


def _b64(data):
    import base64
    return base64.b64encode(data).decode()
