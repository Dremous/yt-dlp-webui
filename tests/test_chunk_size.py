"""`HTTP_CHUNK_SIZE` 解析的单元测试。

这个值是给 yt-dlp 的 `http_chunk_size` 用的：写错不会报错，只会静默退回「不分块」，
所以单位换算必须测住——20G 写成 1024K 或写个 "512MB" 都会悄悄改变下载行为。
"""

import sys
import types
import unittest

# 必须在 import app.downloader 之前顶替 yt_dlp（downloader 顶层会 import 它）
if "yt_dlp" not in sys.modules:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = object
    sys.modules["yt_dlp"] = _stub

from app.downloader import _parse_size


class ParseSizeTest(unittest.TestCase):
    def test_megabytes(self):
        self.assertEqual(_parse_size("512M"), 512 * 1024 ** 2)
        self.assertEqual(_parse_size("32m"), 32 * 1024 ** 2)

    def test_kilobytes_and_gigabytes(self):
        self.assertEqual(_parse_size("1024K"), 1024 * 1024)
        self.assertEqual(_parse_size("1g"), 1024 ** 3)

    def test_plain_bytes(self):
        self.assertEqual(_parse_size("1048576"), 1048576)

    def test_float_with_unit(self):
        # 1.5M 这种写法也得认，否则用户按习惯写就被静默忽略
        self.assertEqual(_parse_size("1.5M"), int(1.5 * 1024 ** 2))

    def test_disabled_values(self):
        for raw in ("0", "off", "none", "", None, "  "):
            with self.subTest(raw=raw):
                self.assertEqual(_parse_size(raw), 0)

    def test_garbage_falls_back_to_single_connection(self):
        # 写错不能抛异常：一个手误不该让整个容器起不来
        for raw in ("512MB", "abc", "12x", "-"):
            with self.subTest(raw=raw):
                self.assertEqual(_parse_size(raw), 0)

    def test_negative_falls_back_to_single_connection(self):
        # 负值放行会送进 yt-dlp 的 random.randint(int(size*0.95), size)，
        # 在 randrange 里抛 ValueError，让之后每一个任务都失败
        for raw in ("-1", "-5M", "-0.5M"):
            with self.subTest(raw=raw):
                self.assertEqual(_parse_size(raw), 0)

    def test_non_finite_falls_back_to_single_connection(self):
        # inf 走 int(float(...)) 会抛 OverflowError；那是模块级常量，
        # 抛在 import 阶段就是容器起不来，比配置无效严重得多
        for raw in ("inf", "Infinity", "nan"):
            with self.subTest(raw=raw):
                self.assertEqual(_parse_size(raw), 0)


if __name__ == "__main__":
    unittest.main()
