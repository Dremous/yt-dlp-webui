"""断点续传接管与代理协议规范化的单元测试。

本地不装 yt-dlp，用最小 stub 顶替；这里只验证文件搬迁与字符串规范化，
不涉及真实网络下载。
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

# 必须在 import app.downloader 之前顶替 yt_dlp（downloader 顶层会 import 它）
if "yt_dlp" not in sys.modules:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = object
    sys.modules["yt_dlp"] = _stub

from app.downloader import DownloadManager, DownloadTask, normalize_proxy


class NormalizeProxyTest(unittest.TestCase):
    def test_socks5_upgraded_to_socks5h(self):
        # socks5 让客户端本地解析域名，容器里容易连到被污染/离代理很远的 IP
        self.assertEqual(
            normalize_proxy("socks5://Clash:secret@192.168.0.30:7893"),
            "socks5h://Clash:secret@192.168.0.30:7893",
        )

    def test_socks5h_kept_as_is(self):
        self.assertEqual(
            normalize_proxy("socks5h://192.168.0.30:7893"),
            "socks5h://192.168.0.30:7893",
        )

    def test_http_untouched(self):
        self.assertEqual(
            normalize_proxy("http://host.docker.internal:7890"),
            "http://host.docker.internal:7890",
        )

    def test_scheme_less_defaults_to_http(self):
        self.assertEqual(
            normalize_proxy("192.168.0.30:7890"), "http://192.168.0.30:7890"
        )

    def test_unknown_scheme_rejected(self):
        with self.assertRaises(ValueError):
            normalize_proxy("ftp://192.168.0.30:21")

    def test_saved_proxy_is_socks5h(self):
        """落盘与运行时都用升级后的地址，老配置不必手改。"""
        with tempfile.TemporaryDirectory() as tmp:
            proxy_file = Path(tmp) / "proxy.txt"
            manager = DownloadManager(Path(tmp), proxy_file=str(proxy_file))
            saved = manager.save_proxy("socks5://u:p@192.168.0.30:7893")
            self.assertEqual(saved, "socks5h://u:p@192.168.0.30:7893")
            self.assertEqual(manager.proxy, saved)
            self.assertIn("socks5h://", proxy_file.read_text(encoding="utf-8"))


class AdoptPartialTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.manager = DownloadManager(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _task(self, task_id: str, status: str,
              url: str = "https://example.com/v", fmt: str = "mp4"):
        task = DownloadTask(task_id, url, fmt)
        task.status = status
        with self.manager._lock:
            self.manager.tasks[task_id] = task
        return task

    def _part(self, task_id: str, name: str, size: int) -> Path:
        task_dir = self.root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / name
        path.write_bytes(b"x" * size)
        return path

    def test_partial_from_failed_task_is_adopted(self):
        self._task("old", "failed")
        old_part = self._part("old", "video [id].f701.mp4.part", 1024)
        task = self._task("new", "downloading")
        new_dir = self.root / "new"
        new_dir.mkdir()

        summary = self.manager._adopt_partial(task, new_dir)

        self.assertFalse(old_part.exists(), "残片应被搬走，而不是复制")
        self.assertTrue((new_dir / old_part.name).exists())
        self.assertIn("1 个分片", summary)
        self.assertIn("0.0 MiB", summary)

    def test_active_task_partial_is_left_alone(self):
        self._task("busy", "downloading")
        busy_part = self._part("busy", "video [id].f701.mp4.part", 1024)
        task = self._task("new", "downloading")
        new_dir = self.root / "new"
        new_dir.mkdir()

        self.assertIsNone(self.manager._adopt_partial(task, new_dir))
        self.assertTrue(busy_part.exists(), "活动写者的 .part 不能被抢")
        self.assertEqual(list(new_dir.iterdir()), [])

    def test_other_url_is_not_adopted(self):
        self._task("old", "failed", url="https://example.com/other")
        self._part("old", "video [id].f701.mp4.part", 1024)
        task = self._task("new", "downloading")
        new_dir = self.root / "new"
        new_dir.mkdir()

        self.assertIsNone(self.manager._adopt_partial(task, new_dir))

    def test_other_format_is_not_adopted(self):
        # 4K 和 1080p 的 outtmpl 相同（都是 Title [id].mp4），残片名也一样；
        # 但两条流的字节内容不同，接管后按 Range 续传会拼出损坏的视频
        self._task("old", "failed", fmt="mp4_2160")
        old_part = self._part("old", "Title [id].mp4.part", 1024)
        task = self._task("new", "downloading", fmt="mp4_1080")
        new_dir = self.root / "new"
        new_dir.mkdir()

        self.assertIsNone(self.manager._adopt_partial(task, new_dir))
        self.assertTrue(old_part.exists(), "不同格式的残片应留在原目录")
        self.assertEqual(list(new_dir.iterdir()), [])

    def test_largest_duplicate_wins(self):
        # 同一个链接失败过两次，两份同名残片只有一份能搬，取最接近下完的
        self._task("old1", "failed")
        self._task("old2", "failed")
        self._part("old1", "video [id].f701.mp4.part", 10)
        big = self._part("old2", "video [id].f701.mp4.part", 20)
        task = self._task("new", "downloading")
        new_dir = self.root / "new"
        new_dir.mkdir()

        summary = self.manager._adopt_partial(task, new_dir)

        self.assertIn("1 个分片", summary)
        self.assertEqual((new_dir / big.name).stat().st_size, 20)

    def test_audio_and_video_partials_both_adopted(self):
        self._task("old", "failed")
        self._part("old", "video [id].f701.mp4.part", 30)
        self._part("old", "audio [id].f140.m4a.part", 5)
        task = self._task("new", "downloading")
        new_dir = self.root / "new"
        new_dir.mkdir()

        summary = self.manager._adopt_partial(task, new_dir)

        self.assertIn("2 个分片", summary)
        # 视频下完才会轮到音频，但两个残片都接管过来，音频一步就能接着下
        self.assertEqual(len(list(new_dir.glob('*.part'))), 2)

    def test_fragments_and_ytdl_are_adopted(self):
        """HLS/DASH 分片是 `.part-FragN`，断点续传也必须接管它们。"""
        self._task("old", "failed")
        frag = self._part("old", "video [id].f701.mp4.part-Frag12", 1024)
        ytdl = self._part("old", "video [id].f701.mp4.ytdl", 20)
        final = self._part("old", "video [id].mp4", 30)
        task = self._task("new", "downloading")
        new_dir = self.root / "new"
        new_dir.mkdir()

        summary = self.manager._adopt_partial(task, new_dir)

        self.assertIn("2 个分片", summary)
        self.assertFalse(frag.exists())
        self.assertFalse(ytdl.exists())
        self.assertTrue((new_dir / frag.name).exists())
        self.assertTrue((new_dir / ytdl.name).exists())
        self.assertTrue(final.exists(), "成品文件不能当临时文件搬走")
        self.assertFalse((new_dir / final.name).exists())

    def test_existing_target_not_overwritten(self):
        self._task("old", "failed")
        self._part("old", "video [id].f701.mp4.part", 10)
        task = self._task("new", "downloading")
        new_dir = self.root / "new"
        new_dir.mkdir()
        (new_dir / "video [id].f701.mp4.part").write_bytes(b"y" * 7)

        self.assertIsNone(self.manager._adopt_partial(task, new_dir))
        self.assertEqual((new_dir / "video [id].f701.mp4.part").stat().st_size, 7)


if __name__ == "__main__":
    unittest.main()
