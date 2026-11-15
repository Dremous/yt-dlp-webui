"""补充代理规范化与不同 URL 不接管残片的回归测试。"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

if "yt_dlp" not in sys.modules:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = object
    sys.modules["yt_dlp"] = _stub

from app.downloader import DownloadManager, DownloadTask, normalize_proxy


class ProxyNormalizeExtraTest(unittest.TestCase):
    def test_socks5h_kept_as_is(self):
        self.assertEqual(
            normalize_proxy("socks5h://192.168.0.30:7893"),
            "socks5h://192.168.0.30:7893",
        )

    def test_saved_proxy_is_socks5h(self):
        with tempfile.TemporaryDirectory() as tmp:
            proxy_file = Path(tmp) / "proxy.txt"
            manager = DownloadManager(Path(tmp), proxy_file=str(proxy_file))
            saved = manager.save_proxy("socks5://u:p@192.168.0.30:7893")
            self.assertEqual(saved, "socks5h://u:p@192.168.0.30:7893")
            self.assertEqual(manager.proxy, saved)
            self.assertIn("socks5h://", proxy_file.read_text(encoding="utf-8"))


class AdoptPartialExtraTest(unittest.TestCase):
    def test_other_url_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = DownloadManager(root)
            old = DownloadTask("old", "https://example.com/other", "mp4")
            old.status = "failed"
            task = DownloadTask("new", "https://example.com/v", "mp4")
            task.status = "downloading"
            with manager._lock:
                manager.tasks["old"] = old
                manager.tasks["new"] = task
            old_dir = root / "old"
            old_dir.mkdir()
            (old_dir / "v.mp4.part").write_bytes(b"x")
            new_dir = root / "new"
            new_dir.mkdir()

            self.assertIsNone(manager._adopt_partial(task, new_dir))
            self.assertTrue((old_dir / "v.mp4.part").exists())


if __name__ == "__main__":
    unittest.main()
