#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""造演示数据：模拟 DJI SMB 上传落盘结构，用于本地 UI 验收"""
import io
import os
import struct
import subprocess
import time
from PIL import Image, ImageDraw

ROOT = os.path.expanduser("~/tmp-dji-demo/DJI Device/Osmo Pocket 4 Pro")


def gradient(path, w, h, c1, c2, label, when, sub="Osmo Pocket 4 Pro"):
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(h - 1, 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    d.rectangle([0, h - 90, w, h], fill=(0, 0, 0))
    d.text((24, h - 66), label, fill=(255, 255, 255))
    d.text((24, h - 38), f"{sub} · {when}", fill=(200, 200, 200))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, "JPEG", quality=88)
    stamp = time.mktime(time.strptime(when, "%Y-%m-%d %H:%M"))
    os.utime(path, (stamp, stamp))


def fake_dng(path, when, label):
    im = Image.new("RGB", (320, 240), (90, 40, 130))
    d = ImageDraw.Draw(im)
    d.text((20, 110), "RAW " + label, fill=(255, 255, 255))
    bio = io.BytesIO()
    im.save(bio, "JPEG", quality=90)
    jpeg = bio.getvalue()

    def entry(tag, typ, cnt, val):
        return struct.pack("<HHI", tag, typ, cnt) + struct.pack("<I", val)
    # 布局：header 8B + IFD0(2+7*12+4=90, @8) + SubIFD(2+7*12+4=90, @98) + JPEG(@188)
    JPEG_OFF = 188
    ifd0 = (struct.pack("<H", 7) + entry(254, 4, 1, 0) + entry(256, 3, 1, 320)
            + entry(257, 3, 1, 240) + entry(259, 3, 1, 1) + entry(273, 4, 1, 0)
            + entry(279, 4, 1, 1000) + entry(330, 4, 1, 98) + struct.pack("<I", 0))
    sub = (struct.pack("<H", 7) + entry(254, 4, 1, 1) + entry(256, 3, 1, 320)
           + entry(257, 3, 1, 240) + entry(259, 3, 1, 6) + entry(273, 4, 1, JPEG_OFF)
           + entry(279, 4, 1, len(jpeg)) + entry(284, 3, 1, 2) + struct.pack("<I", 0))
    assert len(ifd0) == 90 and len(sub) == 90, (len(ifd0), len(sub))
    buf = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8) + ifd0 + sub + jpeg
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(buf)
    stamp = time.mktime(time.strptime(when, "%Y-%m-%d %H:%M"))
    os.utime(path, (stamp, stamp))


def video(path, src, when):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"{src}=duration=4:size=640x360:rate=24",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", path], check=True)
    stamp = time.mktime(time.strptime(when, "%Y-%m-%d %H:%M"))
    os.utime(path, (stamp, stamp))


def main():
    base = os.path.expanduser("~/tmp-dji-demo")
    subprocess.run(["rm", "-rf", base], check=True)
    d1 = os.path.join(ROOT, "2026-10-23")
    d2 = os.path.join(ROOT, "2026-10-24")
    gradient(os.path.join(d1, "DJI_0001.JPG"), 1280, 960, (250, 170, 90), (90, 60, 110), "海边日出", "2026-10-23 06:12")
    gradient(os.path.join(d1, "DJI_0002.JPG"), 1280, 960, (40, 90, 150), (180, 220, 240), "海湾航拍", "2026-10-23 08:47")
    gradient(os.path.join(d1, "DJI_0003.JPG"), 1280, 960, (30, 40, 50), (120, 130, 140), "夜市霓虹", "2026-10-23 21:05")
    gradient(os.path.join(d1, "PANO_0004.JPG"), 2400, 1000, (250, 140, 60), (60, 130, 190), "山顶全景（等距柱状）", "2026-10-23 17:30")
    gradient(os.path.join(d2, "DJI_0005.JPG"), 1280, 960, (140, 190, 120), (60, 90, 60), "竹林小径", "2026-10-24 10:20")
    gradient(os.path.join(d2, "DJI_0006.JPG"), 1280, 960, (230, 200, 150), (170, 120, 90), "街头美食", "2026-10-24 12:40")
    gradient(os.path.join(d2, "DJI_0007.JPG"), 1280, 960, (90, 60, 130), (230, 150, 190), "日落栈道", "2026-10-24 18:55")
    gradient(os.path.join(d2, "DJI_0008.JPG"), 1280, 960, (200, 220, 235), (110, 150, 190), "雨天车窗", "2026-10-24 15:10")
    fake_dng(os.path.join(d2, "DJI_0009.dng"), "2026-10-24 13:02", "RAW+JPG 连拍")
    video(os.path.join(d1, "DJI_0010.MP4"), "testsrc2", "2026-10-23 09:15")
    video(os.path.join(d2, "DJI_0011.MP4"), "smptehdbars", "2026-10-24 11:00")
    with open(os.path.join(d2, "DJI_0011.srt"), "w") as f:
        f.write("1\n00:00:00,000 --> 00:00:02,000\nGPS +31.2304 +121.4737\n")
    print("demo data ready:", base)


if __name__ == "__main__":
    main()
