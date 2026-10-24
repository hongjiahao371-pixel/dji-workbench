# -*- coding: utf-8 -*-
"""单元测试：文件完整性 / DNG 预览提取 / GPS / 配置"""
import os
import struct
import tempfile
import unittest

from app.web_app import (DEFAULT_CONFIG, load_config, looks_complete, probe_gps_mp4,
                         save_config, taken_at_str, extract_dng_preview)


class LooksCompleteTest(unittest.TestCase):
    def _write(self, data):
        fd, p = tempfile.mkstemp()
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        self.addCleanup(os.remove, p)
        return p

    def test_mp4_with_moov_ok(self):
        data = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * 1024 + b"\x00\x00\x00\x30moovxxxx"
        p = self._write(data)
        self.assertTrue(looks_complete(p, ".mp4"))

    def test_mp4_without_moov_is_uploading(self):
        data = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00" + b"\x00" * 2048
        p = self._write(data)
        self.assertFalse(looks_complete(p, ".mp4"))

    def test_mp4_empty(self):
        p = self._write(b"")
        self.assertFalse(looks_complete(p, ".mp4"))

    def test_mp4_no_ftyp(self):
        p = self._write(b"\x00" * 1024 + b"moov")
        self.assertFalse(looks_complete(p, ".mp4"))

    def test_jpeg_complete(self):
        p = self._write(b"\xff\xd8\xff\xe0" + b"\x00" * 128 + b"\xff\xd9")
        self.assertTrue(looks_complete(p, ".jpg"))

    def test_jpeg_truncated(self):
        p = self._write(b"\xff\xd8\xff\xe0" + b"\x00" * 128)
        self.assertFalse(looks_complete(p, ".jpg"))

    def test_dng_magic(self):
        p = self._write(b"II*\x00" + b"\x00" * 64)
        self.assertTrue(looks_complete(p, ".dng"))


class GpsProbeTest(unittest.TestCase):
    def _file_with(self, payload):
        fd, p = tempfile.mkstemp(suffix=".mp4")
        with os.fdopen(fd, "wb") as f:
            f.write(b"\x00\x00\x00\x20\x00\x00\x00\x00\xa9xyz" + b"\x00\x00\x00\x00"
                    + payload + b"\x00" * 64)
        self.addCleanup(os.remove, p)
        return p

    def test_xyz_decimal_degrees(self):
        # 与 ExifTool ConvertISO6709 格式一对齐：+DD.DDD+DDD.DDD+AA.AAA
        from app.web_app import probe_gps_mp4
        lat, lon = probe_gps_mp4(self._file_with(b"+31.2304+121.4737+0.000/"))
        self.assertAlmostEqual(lat, 31.2304, places=4)
        self.assertAlmostEqual(lon, 121.4737, places=4)

    def test_xyz_degrees_minutes(self):
        # 格式二：+DDMM.MMM+DDDMM.MMM
        from app.web_app import parse_iso6709
        lat, lon = parse_iso6709(b"+3123.82+12147.42+0.000/")
        self.assertAlmostEqual(lat, 31 + 23.82 / 60, places=5)
        self.assertAlmostEqual(lon, 121 + 47.42 / 60, places=5)

    def test_xyz_dms(self):
        # 格式三：+DDMMSS.SSS+DDDMMSS.SSS
        from app.web_app import parse_iso6709
        lat, lon = parse_iso6709(b"+312349.2+1214728.5/")
        self.assertAlmostEqual(lat, 31 + 23 / 60 + 49.2 / 3600, places=5)
        self.assertAlmostEqual(lon, 121 + 47 / 60 + 28.5 / 3600, places=5)

    def test_negative_coords(self):
        from app.web_app import parse_iso6709
        lat, lon = parse_iso6709(b"-33.8688-151.2093+5.0/")
        self.assertAlmostEqual(lat, -33.8688, places=4)
        self.assertAlmostEqual(lon, -151.2093, places=4)

    def test_no_gps(self):
        from app.web_app import probe_gps_mp4
        lat, lon = probe_gps_mp4(self._file_with(b"\x00" * 32))
        self.assertIsNone(lat)
        self.assertIsNone(lon)


def _tiff_entry(tag, typ, count, value_u32):
    return struct.pack("<HHI", tag, typ, count) + struct.pack("<I", value_u32)


def build_fake_dng(jpeg_bytes, jpeg_off):
    """构造一个最小 DNG/TIFF：IFD0(原始图占位) + SubIFD(JPEG 预览)"""
    # IFD0 @8: 7 个条目；SubIFD @98: 6 个条目；JPEG @jpeg_off
    ifd0 = (struct.pack("<H", 7)
            + _tiff_entry(254, 4, 1, 0)          # NewSubfileType = 主图
            + _tiff_entry(256, 3, 1, 100)        # ImageWidth
            + _tiff_entry(257, 3, 1, 100)        # ImageLength
            + _tiff_entry(259, 3, 1, 1)          # Compression = 无压缩(占位RAW)
            + _tiff_entry(273, 4, 1, 0)          # StripOffsets 占位
            + _tiff_entry(279, 4, 1, 1000)       # StripByteCounts 占位
            + _tiff_entry(330, 4, 1, 98)         # SubIFDs -> 98
            + struct.pack("<I", 0))              # next IFD = 0
    assert len(ifd0) == 90, len(ifd0)
    sub = (struct.pack("<H", 6)
           + _tiff_entry(254, 4, 1, 1)           # NewSubfileType = 缩小预览
           + _tiff_entry(256, 3, 1, 64)
           + _tiff_entry(257, 3, 1, 48)
           + _tiff_entry(259, 3, 1, 6)           # Compression = JPEG
           + _tiff_entry(273, 4, 1, jpeg_off)    # StripOffsets -> JPEG
           + _tiff_entry(279, 4, 1, len(jpeg_bytes))
           + struct.pack("<I", 0))
    assert len(sub) == 78, len(sub)
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    buf = bytearray(header + ifd0 + sub)
    assert len(buf) == jpeg_off, len(buf)
    buf += jpeg_bytes
    return bytes(buf)


class DngPreviewTest(unittest.TestCase):
    def test_extract_embedded_preview(self):
        from PIL import Image
        import io
        im = Image.new("RGB", (64, 48), (200, 30, 40))
        bio = io.BytesIO()
        im.save(bio, "JPEG", quality=90)
        jpeg = bio.getvalue()
        dng = build_fake_dng(jpeg, jpeg_off=176)
        fd, p = tempfile.mkstemp(suffix=".dng")
        with os.fdopen(fd, "wb") as f:
            f.write(dng)
        self.addCleanup(os.remove, p)
        blob = extract_dng_preview(p)
        self.assertIsNotNone(blob)
        self.assertTrue(blob[:2] == b"\xff\xd8")
        self.assertEqual(len(blob), len(jpeg))
        back = Image.open(io.BytesIO(blob))
        self.assertEqual(back.size, (64, 48))

    def test_garbage_returns_none(self):
        fd, p = tempfile.mkstemp(suffix=".dng")
        with os.fdopen(fd, "wb") as f:
            f.write(b"\x00" * 512)
        self.addCleanup(os.remove, p)
        self.assertIsNone(extract_dng_preview(p))


class TakenAtTest(unittest.TestCase):
    def test_day_folder_wins(self):
        s = taken_at_str("2026-10-24", 1760000000.0)
        self.assertTrue(s.startswith("2026-10-24 "))
        self.assertRegex(s, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_fallback_mtime(self):
        s = taken_at_str("", 1760000000.0)
        self.assertTrue(s.startswith("2025-10-"))  # 1760000000 ≈ 2025-10-09 (本地时区)

    def test_non_date_day_folder(self):
        s = taken_at_str("100MEDIA", 1760000000.0)
        self.assertTrue(s.startswith("2025-10-"))


class ConfigTest(unittest.TestCase):
    def test_load_merge_and_env(self):
        tmp = tempfile.mkdtemp(prefix="djiwb-cfg-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        save_config(tmp, {"port": 9001, "watch_roots": ["/a"]})
        old = os.environ.get("DJI_WB_ROOTS")
        os.environ["DJI_WB_ROOTS"] = "/b:/a"
        try:
            cfg = load_config(__import__("pathlib").Path(tmp))
        finally:
            if old is None:
                os.environ.pop("DJI_WB_ROOTS", None)
            else:
                os.environ["DJI_WB_ROOTS"] = old
        self.assertEqual(cfg["port"], 9001)
        self.assertEqual(cfg["watch_roots"], ["/a", "/b"])  # env 追加且去重
        self.assertEqual(cfg["proxy_crf"], DEFAULT_CONFIG["proxy_crf"])  # 默认值补全


if __name__ == "__main__":
    unittest.main()
