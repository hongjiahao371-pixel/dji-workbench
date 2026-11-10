"""na_effects —— native 引擎全量特效映射表。

concat 目录 127 项 → ffmpeg 实现：
- 23 video effects：逐条映射 ffmpeg 滤镜链（含参数插值）
- 57 filter 风格预设：eq/colorbalance/hue 组合近似（按名称语义调参）
- 25 audio effects：ffmpeg 音频滤镜链（含参数插值）
- 22 transitions：v1 已支持 fade/cross-fade/dissolve/fade-black/fade-white；
  其余转场为合成器级（xfade 家族），v2.1 接入 xfade（见 na_engine 转场段）

映射原则：可用 ffmpeg 原生滤镜精确/近似实现的全部实现；
纯合成器效果（bloom-pulse 的动画、glitch 家族）用最近似静态滤镜。
"""

# ---------- 23 个视频效果：id → (基础链模板, 参数引用) ----------
# 模板里 {W}{H} 会被替换；params 通过 pr 字典插值。
def video_effect_chain(eid, pr, W, H):
    g = lambda k, d: float(pr.get(k, d))
    if eid == "concat.bloom-pulse":
        # 泛光脉冲：提亮+柔光（静态近似）
        a = max(0.1, g("amount", 50) / 50)
        return f"split[b_a][b_b];[b_b]gblur=sigma={max(4, int(12 * a))},eq=brightness={0.12 * a:.2f}[b_g];[b_a][b_g]blend=all_mode=screen:all_opacity={min(0.8, 0.4 * a):.2f}"
    if eid == "concat.chroma-key":
        col = int(pr.get("color", 11616511) or 11616511)
        r, gg, b = (col >> 16) & 255, (col >> 8) & 255, col & 255
        sim = max(0.01, g("similarity", 30) / 100)
        return (f"chromakey=color=0x{r:02X}{gg:02X}{b:02X}:similarity={sim:.2f}:blend={min(0.5, g('softness', 10) / 100):.2f}")
    if eid == "concat.chromatic-aberration":
        s = max(0, min(20, int(g("shift", 4))))
        return (f"split[c_a][c_b];[c_b]crop=iw-{s}:ih:0:0,pad=iw:{s}:0:{s}:color=red@0[ch_r];"
                f"[c_a][ch_r]blend=all_mode=screen:all_opacity=0.35")
    if eid == "concat.color-wheels":
        lift = g("lift", 0); gamma = g("gamma", 0); gain = g("gain", 0)
        return (f"curves=all='{max(0, 1 + lift * 0.5 - gamma * 0.2):.3f}/{min(1, 1 + gain * 0.5 + gamma * 0.2):.3f}'")
    if eid == "concat.contrast":
        a = g("amount", 0)
        return f"eq=contrast={max(0.1, min(3, 1 + a)):.2f}"
    if eid == "concat.corona":
        th = g("threshold", 60) / 100
        rad = max(2, int(g("radius", 18)))
        a = g("amount", 55) / 100
        return (f"split[co_a][co_b];[co_b]lutyuv=y='val({max(0.3, th):.2f}*val)':u=128:v=128,gblur=sigma={rad},"
                f"colorbalance=rs=.2:bs=.15[co_g];[co_a][co_g]blend=all_mode=screen:all_opacity={min(0.9, a):.2f}")
    if eid == "concat.curves":
        # 曲线参数为 wheel/curve 复杂对象——v1 用 gamma 近似
        return "eq=gamma=1.1"
    if eid == "concat.exposure":
        st = max(-3, min(3, g("stops", 0)))
        return f"eq=exposure={st:.2f}"
    if eid == "concat.film-grain":
        a = max(1, min(60, int(g("amount", 12))))
        return f"noise=alls={a}:allf=t+u"
    if eid == "concat.fisheye":
        st = max(0.1, g("strength", 50) / 50)
        return f"v360=input=sg:output=sg:d_fov={120 * min(2, st):.1f}"
    if eid == "concat.gaussian-blur":
        r = max(0.5, min(40, g("radius", 10)))
        return f"gblur=sigma={r:.1f}"
    if eid == "concat.glow":
        a = g("amount", 45) / 100
        return (f"split[gl_a][gl_b];[gl_b]gblur=sigma=14,eq=brightness={0.15 * a:.2f}[gl_g];"
                f"[gl_a][gl_g]blend=all_mode=screen:all_opacity={min(0.85, 0.5 + a / 2):.2f}")
    if eid == "concat.halation":
        th = g("threshold", 55) / 100
        rad = max(2, int(g("radius", 22)))
        a = g("amount", 45) / 100
        tint = g("tint", 0)
        rs = 0.25 + tint / 100 * 0.3
        return (f"split[ha_a][ha_b];[ha_b]lutyuv=y='val({max(0.3, th):.2f}*val)',gblur=sigma={rad},"
                f"colorbalance=rs={rs:.2f}:gs=.05[ha_g];[ha_a][ha_g]blend=all_mode=screen:all_opacity={min(0.9, a):.2f}")
    if eid == "concat.halftone":
        sz = max(2, int(g("size", 6)))
        # 近似：降采样+阈值化像素网点
        return (f"scale=iw/{sz}:ih/{sz}:flags=neighbor,format=gray,"
                f"eq=contrast=2.2,scale={W}:{H}:flags=neighbor")
    if eid == "concat.luma-key":
        th = g("threshold", 10); soft = g("softness", 10); inv = bool(pr.get("invert", 0))
        lo = max(0, min(255, int(th * 2.55)))
        op = "ge" if inv else "le"
        return (f"lutyuv=y='if({op}(val\\,{lo})\\,255\\,0)',format=rgba,colorkey=0x000000:blend={min(0.9, soft / 100 + 0.1):.2f}")
    if eid == "concat.motion-blur":
        ln = max(2, int(g("length", 18)))
        return f"tmix=frames={min(12, max(2, ln // 4))}:weights='1 1 1 1'"
    if eid == "concat.pixelate":
        sz = max(2, int(g("size", 16)))
        return f"scale=iw/{sz}:ih/{sz}:flags=neighbor,scale={W}:{H}:flags=neighbor"
    if eid == "concat.posterize":
        lv = max(2, min(32, int(g("levels", 4))))
        step = 255 // lv
        return (f"lutyuv=r='trunc(val/{step})*{step}':g='trunc(val/{step})*{step}':b='trunc(val/{step})*{step}'")
    if eid == "concat.saturation":
        a = g("amount", 0)
        return f"eq=saturation={max(0, min(3, 1 + a)):.2f}"
    if eid == "concat.sharpen":
        a = max(0, min(4, g("amount", 1)))
        return f"unsharp=5:5:{a:.2f}"
    if eid == "concat.vignette":
        s = max(0.1, g("strength", 50) / 50)
        return f"vignette=angle=PI/{max(2, 8 - int(s * 4))}"
    if eid == "concat.white-balance":
        temp = g("temperature", 6500)
        tint = g("tint", 0)
        k = (temp - 6500) / 6500
        rs = max(-0.3, min(0.3, k * 0.3 + tint / 200))
        bs = max(-0.3, min(0.3, -k * 0.3 - tint / 200))
        return f"colortemperature=temperature={int(max(2000, min(12000, temp)))}:pl={1 + tint / 300:.2f},colorbalance=rs={rs:.2f}:bs={bs:.2f}"
    if eid == "concat.zoom-blur":
        a = max(1, min(40, int(g("amount", 25) / 2)))
        return f"gblur=sigma={a}:steps=4"   # 径向近似：多步模糊
    return None


# ---------- 57 个滤镜风格：id → eq/colorbalance/hue 链 ----------
# 每条 = (eq 串, colorbalance 串, hue 串) 按需为空串；语义近似调参。
FILTER_LOOKS = {
    "concat.adjust":          ("", "", ""),
    "concat.autumn":          ("eq=saturation=1.25:contrast=1.08", "colorbalance=rs=.14:gs=.02:bs=-.16", ""),
    "concat.bleach-bypass":   ("eq=contrast=1.45:saturation=0.45:brightness=0.06", "", ""),
    "concat.bronze":          ("eq=saturation=0.75:contrast=1.12", "colorbalance=rs=.18:gs=.06:bs=-.18", ""),
    "concat.classic-chrome":  ("eq=saturation=0.62:contrast=1.2", "colorbalance=rs=.04:gs=-.02:bs=-.06", ""),
    "concat.clean":           ("eq=contrast=1.05:saturation=1.1", "", ""),
    "concat.cloudless-sky":   ("eq=saturation=1.18:brightness=0.04", "colorbalance=bs=.14:gs=.02", ""),
    "concat.contrast-pop":    ("eq=contrast=1.4:saturation=1.22", "", ""),
    "concat.cool":            ("eq=saturation=0.92", "colorbalance=rs=-.12:bs=.16", ""),
    "concat.cool-blue":       ("eq=saturation=1.05:contrast=1.06", "colorbalance=rs=-.18:bs=.22", ""),
    "concat.cyberpunk":       ("eq=saturation=1.6:contrast=1.25", "colorbalance=rs=.16:bs=.24:gs=-.1", "hue=-12"),
    "concat.daydream":        ("eq=saturation=0.8:brightness=0.08:contrast=0.94", "colorbalance=rs=.1:bs=.08", ""),
    "concat.delicious":       ("eq=saturation=1.28:contrast=1.1", "colorbalance=rs=.14:gs=.04:bs=-.06", ""),
    "concat.digicam":         ("eq=contrast=1.04:saturation=1.04", "", ""),
    "concat.dreamy":          ("eq=saturation=0.72:brightness=0.1:contrast=0.9", "colorbalance=rs=.12:bs=.14", ""),
    "concat.fair":            ("eq=saturation=0.82:brightness=0.09", "colorbalance=rs=.05:gs=.03:bs=.04", ""),
    "concat.falling-dust":    ("eq=saturation=0.9:brightness=0.05", "", "hue=8"),
    "concat.film":            ("eq=saturation=0.82:contrast=1.14:gamma=0.98", "colorbalance=rs=.07:gs=-.01:bs=-.07", ""),
    "concat.fresh":           ("eq=saturation=1.22:contrast=1.05:brightness=0.03", "colorbalance=gs=.08:bs=.04", ""),
    "concat.golden-hour":     ("eq=saturation=1.18:contrast=1.08", "colorbalance=rs=.2:gs=.05:bs=-.2", "hue=-6"),
    "concat.high-contrast-mono": ("hue=s=0,eq=contrast=1.55", "", ""),
    "concat.ivory":           ("eq=saturation=0.6:brightness=0.12:contrast=0.95", "colorbalance=rs=.08:gs=.08:bs=.05", ""),
    "concat.lagoon":          ("eq=saturation=1.3:contrast=1.1", "colorbalance=rs=-.1:gs=.04:bs=.2", ""),
    "concat.landscape-pop":   ("eq=saturation=1.4:contrast=1.18", "", ""),
    "concat.marquee":         ("eq=saturation=1.15:contrast=1.3", "colorbalance=rs=.1:bs=-.05", ""),
    "concat.matcha":          ("eq=saturation=0.85", "colorbalance=gs=.16:rs=-.06:bs=-.04", ""),
    "concat.midnight":        ("eq=saturation=0.85:brightness=-.1:contrast=1.25", "colorbalance=bs=.2:rs=-.08", ""),
    "concat.milk":            ("eq=saturation=0.5:brightness=0.15:contrast=0.88", "colorbalance=rs=.06:gs=.06:bs=.08", ""),
    "concat.mono":            ("hue=s=0,eq=contrast=1.1", "", ""),
    "concat.moody":           ("eq=saturation=0.7:contrast=1.3:brightness=-.06", "colorbalance=bs=.12:gs=-.04", ""),
    "concat.natural":         ("eq=saturation=1.05:contrast=1.02", "", ""),
    "concat.neon":            ("eq=saturation=1.7:contrast=1.3", "colorbalance=rs=.1:bs=.2", "hue=-15"),
    "concat.night":           ("eq=saturation=0.75:brightness=-.14:contrast=1.28", "colorbalance=bs=.18:rs=-.06", ""),
    "concat.noir":            ("hue=s=0,eq=contrast=1.7:brightness=-.04", "", ""),
    "concat.peach":           ("eq=saturation=0.9:brightness=0.07", "colorbalance=rs=.16:gs=.04:bs=-.05", ""),
    "concat.pink-fuzz":       ("eq=saturation=1.2:brightness=0.06", "colorbalance=rs=.2:bs=.1", "hue=10"),
    "concat.polaroid":        ("eq=saturation=0.72:contrast=1.1:brightness=0.07", "colorbalance=rs=.1:gs=-.02:bs=-.12", ""),
    "concat.rain":            ("eq=saturation=0.8:contrast=1.12:brightness=-.04", "colorbalance=bs=.14:gs=-.02", ""),
    "concat.retro-70s":       ("eq=saturation=0.85:contrast=1.1:gamma=1.05", "colorbalance=rs=.16:gs=.02:bs=-.2", ""),
    "concat.retro-cartoon":   ("eq=saturation=1.5:contrast=1.35", "", ""),
    "concat.rosy":            ("eq=saturation=0.95:brightness=0.06", "colorbalance=rs=.18:gs=-.02:bs=.02", ""),
    "concat.sage":            ("eq=saturation=0.68:contrast=0.98", "colorbalance=gs=.12:rs=-.04:bs=-.02", ""),
    "concat.sci-fi":          ("eq=saturation=1.35:contrast=1.22", "colorbalance=bs=.2:rs=-.1:gs=.06", "hue=-18"),
    "concat.silver":          ("hue=s=0,eq=contrast=1.15:saturation=0", "", ""),
    "concat.soft-light":      ("eq=contrast=0.92:brightness=0.1:saturation=0.9", "", ""),
    "concat.spooky":          ("eq=saturation=0.4:contrast=1.4:brightness=-.1", "colorbalance=gs=-.1:bs=.08", ""),
    "concat.super-8":         ("eq=saturation=0.9:contrast=1.12:gamma=1.06", "colorbalance=rs=.14:bs=-.14", ""),
    "concat.teal-orange":     ("eq=saturation=1.25:contrast=1.18", "colorbalance=rs=.16:bs=-.16:gs=-.02", ""),
    "concat.tropical":        ("eq=saturation=1.45:contrast=1.1", "colorbalance=gs=.1:bs=.08:rs=-.04", ""),
    "concat.umber":           ("eq=saturation=0.7:contrast=1.15", "colorbalance=rs=.14:gs=.02:bs=-.16", ""),
    "concat.vaporwave":       ("eq=saturation=1.55:contrast=1.15", "colorbalance=rs=.14:bs=.24:gs=-.06", "hue=-10"),
    "concat.vhs":             ("eq=saturation=1.1:contrast=1.1:brightness=-.02", "colorbalance=rs=.08:bs=.1", ""),
    "concat.vibrance":        ("eq=saturation=1.3:contrast=1.04", "", ""),
    "concat.vintage":         ("eq=saturation=0.6:contrast=1.08:gamma=1.04", "colorbalance=rs=.12:gs=-.04:bs=-.1", ""),
    "concat.vivid":           ("eq=saturation=1.35:contrast=1.12", "", ""),
    "concat.warm":            ("eq=saturation=1.05", "colorbalance=rs=.15:bs=-.15", ""),
    "concat.winter-cool":     ("eq=saturation=0.8:contrast=1.1:brightness=0.04", "colorbalance=bs=.2:rs=-.1:gs=.02", ""),
}


def filter_look_chain(fid):
    """57 滤镜风格 → (eq, colorbalance, hue) 链。"""
    eq, cb, hue = FILTER_LOOKS.get(fid, ("", "", ""))
    out = []
    if eq: out.append(eq)
    if cb: out.append(cb)
    if hue: out.append(hue)
    return ",".join(out)


# ---------- 25 个音频效果：id → 滤镜链（pr 插值） ----------
def audio_effect_chain(eid, pr):
    g = lambda k, d: float(pr.get(k, d))
    if eid == "concat.bass":
        return f"bass=g={max(-20, min(20, g('gain', 5)))}:f=110"
    if eid == "concat.bright":
        return f"treble=g={max(-20, min(20, g('gain', 5)))}:f=6000"
    if eid in ("concat.cathedral", "concat.hall", "concat.plate", "concat.room", "concat.stadium"):
        size = g("size", 45) / 100
        return (f"aecho=0.8:{size:.2f}:{360 + int(880 * size)}:{0.25 + size * 0.4}:"
                f"{720 + int(1760 * size)}:{0.18 + size * 0.3}")
    if eid == "concat.chipmunk":
        return f"asetrate=44100*{1 + g('pitch', 7) / 24:.3f},aresample=44100,atempo={1 / (1 + g('pitch', 7) / 24):.3f}"
    if eid == "concat.deep":
        return f"asetrate=44100*{1 / (1 + abs(g('pitch', -3)) / 24):.3f},aresample=44100,bass=g={min(12, g('body', 3) * 2)}"
    if eid == "concat.denoise":
        return f"afftdn=nf={max(-20, min(60, int(g('strength', 50) - 20)))}"
    if eid == "concat.distorted":
        return f"aeval='val(0)*{1 + g('drive', 50) / 25:.1f}|val(1)*{1 + g('drive', 50) / 25:.1f}',alimiter=limit=0.9"
    if eid == "concat.echo":
        d = max(0.05, g("delay", 0.25))
        dec = max(0.05, min(0.9, g("decay", 0.4)))
        return f"aecho=0.8:0.9:{int(d * 1000)}:{dec:.2f}:{int(d * 2000)}:{dec * dec:.2f}"
    if eid == "concat.enhance-voice":
        a = g("amount", 60) / 100
        return f"highpass=f=120,lowpass=f=6800,equalizer=f=2800:t=q:w=1:g={3 * a:.1f},dynaudnorm"
    if eid == "concat.lofi":
        bits = max(4, int(g("bits", 8)))
        return f"aformat=sample_fmts=s16:{bits}bit,volume=0.8"
    if eid == "concat.loudness":
        tgt = max(-36, min(-8, g("target", -23)))
        return f"loudnorm=I={tgt:.0f}:TP=-1.5:LRA=11"
    if eid == "concat.megaphone":
        return "highpass=f=600,lowpass=f=2800,aeval='val(0)*0.7|val(1)*0.7',alimiter=limit=0.6"
    if eid == "concat.pitch":
        st = max(-12, min(12, g("semitones", 0)))
        k = 2 ** (st / 12)
        return f"asetrate=44100*{k:.4f},aresample=44100,atempo={1 / k:.4f}"
    if eid == "concat.radio":
        n = g("noise", 30) / 100
        return (f"highpass=f=500,lowpass=f=3000,alimiter=limit=0.5,"
                f"anoisesrc=color=brown:amplitude={max(0.01, n * 0.06):.3f}:d=999999[mixsrc]" )
    if eid == "concat.robot":
        dep = g("depth", 5)
        return f"vibrato=f={max(2, int(dep * 6))}:d={min(1, dep / 8):.2f},alimiter=limit=0.7"
    if eid == "concat.sweet":
        p = g("pitch", 0)
        st = max(-4, min(4, p))
        k = 2 ** (st / 12)
        return f"treble=g=3,asetrate=44100*{k:.4f},aresample=44100"
    if eid == "concat.telephone":
        return "highpass=f=400,lowpass=f=2400,alimiter=limit=0.55,aeval='val(0)*0.8|val(1)*0.8'"
    if eid == "concat.treble":
        return f"treble=g={max(-20, min(20, g('gain', 4)))}:f=5000"
    if eid == "concat.underwater":
        dep = max(1, int(g("depth", 5)))
        return f"lowpass=f={max(300, 3000 - dep * 400)},vibrato=f={dep + 2}:d=0.7"
    if eid == "concat.vinyl":
        w = g("warmth", 5) / 5
        return f"lowpass=f={int(9000 - w * 2500)},bass=g={2 + w * 3:.0f}"
    if eid == "concat.whisper":
        a = g("air", 5) / 5
        return f"highpass=f=200,equalizer=f=9000:t=q:w=2:g={4 * a:.1f},volume=0.85"
    return None


# ---------- 全目录（concat effects_list 同形 127+7） ----------
_ALL = []


def _add(kind, eid, name, params=None):
    _ALL.append({"id": eid, "name": name, "kind": kind, "params": params or []})


_look_names = [
    ("concat.adjust", "Adjust"), ("concat.autumn", "Autumn"),
    ("concat.bleach-bypass", "Bleach Bypass"), ("concat.bronze", "Bronze"),
    ("concat.classic-chrome", "Classic Chrome"), ("concat.clean", "Clean"),
    ("concat.cloudless-sky", "Cloudless Sky"), ("concat.contrast-pop", "Contrast Pop"),
    ("concat.cool", "Cool"), ("concat.cool-blue", "Cool Blue"),
    ("concat.cyberpunk", "Cyberpunk"), ("concat.daydream", "Daydream"),
    ("concat.delicious", "Delicious"), ("concat.digicam", "Digicam"),
    ("concat.dreamy", "Dreamy"), ("concat.fair", "Fair"),
    ("concat.falling-dust", "Falling Dust"), ("concat.film", "Film"),
    ("concat.fresh", "Fresh"), ("concat.golden-hour", "Golden Hour"),
    ("concat.high-contrast-mono", "High-Contrast Mono"), ("concat.ivory", "Ivory"),
    ("concat.lagoon", "Lagoon"), ("concat.landscape-pop", "Vivid Landscape"),
    ("concat.marquee", "Marquee"), ("concat.matcha", "Matcha"),
    ("concat.midnight", "Midnight"), ("concat.milk", "Milk"),
    ("concat.mono", "Mono"), ("concat.moody", "Moody"),
    ("concat.natural", "Natural"), ("concat.neon", "Neon"),
    ("concat.night", "Night"), ("concat.noir", "Noir"),
    ("concat.peach", "Peach"), ("concat.pink-fuzz", "Pink Fuzz"),
    ("concat.polaroid", "Polaroid"), ("concat.rain", "Rain"),
    ("concat.retro-70s", "Retro 70s"), ("concat.retro-cartoon", "Retro Cartoon"),
    ("concat.rosy", "Rosy"), ("concat.sage", "Sage"),
    ("concat.sci-fi", "Sci-Fi"), ("concat.silver", "Silver"),
    ("concat.soft-light", "Soft Light"), ("concat.spooky", "Spooky"),
    ("concat.super-8", "Super 8"), ("concat.teal-orange", "Teal & Orange"),
    ("concat.tropical", "Tropical"), ("concat.umber", "Umber"),
    ("concat.vaporwave", "Vaporwave"), ("concat.vhs", "VHS"),
    ("concat.vibrance", "Vibrance"), ("concat.vintage", "Vintage"),
    ("concat.vivid", "Vivid"), ("concat.warm", "Warm"),
    ("concat.winter-cool", "Winter Cool"),
]

_audio_specs = [
    ("concat.bass", "Bass", [("gain", 5.0)]),
    ("concat.bright", "Bright", [("gain", 5.0)]),
    ("concat.cathedral", "Cathedral", [("size", 55.0)]),
    ("concat.chipmunk", "Chipmunk", [("pitch", 7.0)]),
    ("concat.deep", "Deep", [("pitch", -3.0), ("body", 3.0)]),
    ("concat.denoise", "Noise Reduction", [("strength", 50.0)]),
    ("concat.distorted", "Distorted", [("drive", 50.0)]),
    ("concat.echo", "Echo", [("delay", 0.25), ("decay", 0.4)]),
    ("concat.enhance-voice", "Enhance Voice", [("amount", 60.0)]),
    ("concat.hall", "Hall", [("size", 50.0)]),
    ("concat.lofi", "Lo-fi", [("bits", 8.0)]),
    ("concat.loudness", "Normalize Loudness", [("target", -23.0)]),
    ("concat.megaphone", "Megaphone", [("drive", 5.0)]),
    ("concat.pitch", "Pitch", [("semitones", 0.0)]),
    ("concat.plate", "Plate", [("size", 40.0)]),
    ("concat.radio", "Radio", [("noise", 30.0)]),
    ("concat.robot", "Robot", [("depth", 5.0)]),
    ("concat.room", "Room", [("size", 40.0)]),
    ("concat.stadium", "Stadium", [("size", 6.0)]),
    ("concat.sweet", "Sweet", [("amount", 65.0), ("pitch", 0.0)]),
    ("concat.telephone", "Telephone", [("drive", 3.0)]),
    ("concat.treble", "Treble", [("gain", 4.0)]),
    ("concat.underwater", "Underwater", [("depth", 5.0)]),
    ("concat.vinyl", "Vinyl", [("warmth", 5.0)]),
    ("concat.whisper", "Whisper", [("air", 5.0)]),
]

_transition_names = [
    ("concat.blur-dissolve", "Blur Dissolve"), ("concat.clock-wipe", "Clock Wipe"),
    ("concat.cube", "Cube"), ("concat.dissolve", "Dissolve"),
    ("concat.fade-black", "Fade to Black"), ("concat.fade-white", "Fade to White"),
    ("concat.film-burn", "Film Burn"), ("concat.glitch-transition", "Glitch"),
    ("concat.glitch-warp", "Glitch Warp"), ("concat.iris", "Iris"),
    ("concat.light-leak-flash", "Light Leak Flash"),
    ("concat.light-leak-transition", "Light Leak"),
    ("concat.linear-wipe", "Linear Wipe"), ("concat.push", "Push"),
    ("concat.rgb-split-wipe", "RGB Split"), ("concat.shape-wipe", "Shape Wipe"),
    ("concat.slide", "Slide"), ("concat.spin", "Spin"),
    ("concat.swirl", "Swirl"), ("concat.whip-pan", "Whip Pan"),
    ("concat.zoom-blur-transition", "Zoom Blur"), ("concat.zoom-transition", "Zoom"),
]

# 23 video effects 目录（params 与 concat 对齐）
_effect_catalog = [
    ("concat.bloom-pulse", "Bloom Pulse", [("amount", 50.0), ("speed", 2.0)]),
    ("concat.chroma-key", "Chroma Key", [("color", 11616511.0), ("similarity", 30.0), ("softness", 10.0), ("spill", 50.0)]),
    ("concat.chromatic-aberration", "Chromatic Aberration", [("shift", 4.0)]),
    ("concat.color-wheels", "Color Wheels", [("lift", 0.0), ("gamma", 0.0), ("gain", 0.0)]),
    ("concat.contrast", "Contrast", [("amount", 0.0)]),
    ("concat.corona", "Corona", [("threshold", 60.0), ("radius", 18.0), ("amount", 55.0), ("cool", 70.0)]),
    ("concat.curves", "Curves", [("luma", 0.0), ("red", 0.0), ("green", 0.0), ("blue", 0.0)]),
    ("concat.exposure", "Exposure", [("stops", 0.0)]),
    ("concat.film-grain", "Film Grain", [("amount", 12.0)]),
    ("concat.fisheye", "Fisheye", [("strength", 50.0)]),
    ("concat.gaussian-blur", "Gaussian Blur", [("radius", 10.0)]),
    ("concat.glow", "Glow", [("amount", 45.0)]),
    ("concat.halation", "Halation", [("threshold", 55.0), ("radius", 22.0), ("amount", 45.0), ("tint", 0.0)]),
    ("concat.halftone", "Halftone", [("size", 6.0)]),
    ("concat.luma-key", "Luma Key", [("threshold", 10.0), ("softness", 10.0), ("invert", 0.0)]),
    ("concat.motion-blur", "Motion Blur", [("length", 18.0), ("angle", 0.0)]),
    ("concat.pixelate", "Pixelate", [("size", 16.0)]),
    ("concat.posterize", "Posterize", [("levels", 4.0)]),
    ("concat.saturation", "Saturation", [("amount", 0.0)]),
    ("concat.sharpen", "Sharpen", [("amount", 1.0)]),
    ("concat.vignette", "Vignette", [("strength", 50.0)]),
    ("concat.white-balance", "White Balance", [("temperature", 6500.0), ("tint", 0.0)]),
    ("concat.zoom-blur", "Zoom Blur", [("amount", 25.0), ("x", 50.0), ("y", 50.0)]),
]
for _eid, _nm, _ps in _effect_catalog:
    _add("effect", _eid, _nm, [{"key": k, "type": ("color" if k == "color" else ("bool" if k == "invert" else ("curve" if k in ("luma", "red", "green", "blue") else ("wheel" if k in ("lift", "gamma", "gain") else "float")))), "default": v} for k, v in _ps])

for _eid, _nm in _look_names:
    _add("filter", _eid, _nm, [{"key": "amount", "type": "float", "default": 1.0}])

for _eid, _nm, _ps in _audio_specs:
    _add("audio", _eid, _nm, [{"key": k, "type": "float", "default": v} for k, v in _ps])

for _eid, _nm in _transition_names:
    _add("transition", _eid, _nm)

# 旧 7 项别名（前端下拉向后兼容）
for _eid, _nm in [("concat.bw", "黑白"), ("concat.sepia", "复古"), ("concat.warm", "暖色"),
                  ("concat.cool", "冷色"), ("concat.vignette", "暗角"), ("concat.blur", "模糊"),
                  ("concat.sharpen", "锐化")]:
    _add("effect", _eid, _nm)

ALL_CATALOG = _ALL


# ---------- 22 转场 → xfade transition 名 ----------
def xfade_transition(tid):
    m = {
        "concat.dissolve": "dissolve",
        "concat.fade-black": "fadeblack",
        "concat.fade-white": "fadewhite",
        "concat.blur-dissolve": "hblur",
        "concat.linear-wipe": "wipeleft",
        "concat.clock-wipe": "radial",
        "concat.shape-wipe": "circleopen",
        "concat.iris": "circleopen",
        "concat.push": "smoothleft",
        "concat.slide": "slideleft",
        "concat.whip-pan": "hlslice",
        "concat.rgb-split-wipe": "distance",
        "concat.glitch-transition": "pixelize",
        "concat.glitch-warp": "pixelize",
        "concat.zoom-transition": "zoomin",
        "concat.zoom-blur-transition": "zoomin",
        "concat.film-burn": "fade",
        "concat.light-leak-flash": "fade",
        "concat.light-leak-transition": "fade",
        "concat.spin": "smoothleft",
        "concat.swirl": "circleclose",
        "concat.cube": "smoothright",
    }
    return m.get(tid, "fade")
