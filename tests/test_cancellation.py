"""取消状态机回归测试。

覆盖两个曾经会丢失取消信号的窗口：
- 排队任务在 `_run` 检查之后、`_download` 置为下载中之前被取消；
- 下载完成与取消同时发生，完成状态不能把取消覆盖掉。
"""

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

from app import downloader
from app.downloader import DownloadManager, DownloadTask


class _FakeYDL:
    """只需支持 `_download` 的上下文管理器与 extract_info。"""

    def __init__(self, _opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def extract_info(self, _url, download=True):
        return {"title": "fake"}


class BlockingAdoptManager(DownloadManager):
    """把 `_adopt_partial` 卡住，精确制造「已通过排队检查、尚未开始下载」的窗口。"""

    def __init__(self, download_dir):
        super().__init__(download_dir)
        self.adopt_entered = threading.Event()
        self.adopt_release = threading.Event()

    def _adopt_partial(self, task, task_dir):
        self.adopt_entered.set()
        self.adopt_release.wait(timeout=2)
        return None


class CancellationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.manager = DownloadManager(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _queue(self, task_id="q"):
        task = DownloadTask(task_id, "https://example.com/v", "best")
        with self.manager._lock:
            self.manager.tasks[task_id] = task
        return task

    def test_cancel_queued_task(self):
        task = self._queue()

        self.manager.cancel(task.id)

        self.assertEqual(task.status, "cancelled")

    def test_cancel_rejects_finished_task(self):
        task = self._queue()
        task.status = "finished"

        with self.assertRaises(ValueError):
            self.manager.cancel(task.id)

        self.assertEqual(task.status, "finished")

    def test_cancel_during_setup_is_not_overwritten_by_finish(self):
        manager = BlockingAdoptManager(self.root)
        task = DownloadTask("q", "https://example.com/v", "best")
        with manager._lock:
            manager.tasks[task.id] = task

        thread = threading.Thread(target=manager._run, args=(task,), daemon=True)
        with mock.patch.object(downloader.yt_dlp, "YoutubeDL", _FakeYDL):
            thread.start()
            try:
                self.assertTrue(manager.adopt_entered.wait(2), "未进入 _adopt_partial")
                manager.cancel(task.id)
            finally:
                manager.adopt_release.set()
                thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertEqual(task.status, "cancelled")
        self.assertFalse((self.root / task.id).exists(), "取消后应清理任务目录")


if __name__ == "__main__":
    unittest.main()
