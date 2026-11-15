"""端口解析与并发保存的健壮性回归测试。"""

import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

if "yt_dlp" not in sys.modules:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = object
    sys.modules["yt_dlp"] = _stub

from app.downloader import DownloadManager
from app.serve import _port


class PortTest(unittest.TestCase):
    def test_valid_port(self):
        with mock.patch.dict(os.environ, {"PORT": "9000"}):
            self.assertEqual(_port(), 9000)

    def test_invalid_port_falls_back_to_default(self):
        for value in ("abc", "-1", "0", "65536", ""):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"PORT": value}):
                    self.assertEqual(_port(), 8080)


class AtomicWriteTest(unittest.TestCase):
    def test_concurrent_cookie_saves_do_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie = Path(tmp) / "cookies.txt"
            manager = DownloadManager(Path(tmp), cookie_file=str(cookie))
            barrier = threading.Barrier(2)
            errors = []

            def save(data):
                try:
                    barrier.wait(5)
                    manager.save_cookie(data)
                except Exception as exc:  # noqa: BLE001 - 测试收集线程异常
                    errors.append(exc)

            threads = [
                threading.Thread(target=save, args=(b"# Netscape HTTP Cookie File\n" + b"a" * 100,)),
                threading.Thread(target=save, args=(b"# Netscape HTTP Cookie File\n" + b"b" * 100,)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)

            self.assertEqual(errors, [], "并发保存不应因共用 .tmp 文件名而互相失败")
            self.assertIn(cookie.read_bytes()[:1], (b"#",))

    def test_concurrent_proxy_saves_do_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            proxy = Path(tmp) / "proxy.txt"
            manager = DownloadManager(Path(tmp), proxy_file=str(proxy))
            barrier = threading.Barrier(2)
            errors = []

            def save(url):
                try:
                    barrier.wait(5)
                    manager.save_proxy(url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [
                threading.Thread(target=save, args=("http://127.0.0.1:7890",)),
                threading.Thread(target=save, args=("http://127.0.0.1:7891",)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)

            self.assertEqual(errors, [])
            self.assertIn("127.0.0.1:789", proxy.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
