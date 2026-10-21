"""任务删除行为的单元测试。

本地不装 yt-dlp，用最小 stub 顶替；这里只验证任务字典与磁盘目录的清理策略，
不涉及真实网络下载。

清理规则（与界面的确认文案一致）：
- failed / cancelled：目录里只有 .part、.log 等残留，连目录一起删
- finished：视频文件还有用，只删记录、保留目录
- downloading / queued：必须先取消，拒绝删除
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

from app.downloader import DownloadManager, DownloadTask


class RemovalTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.manager = DownloadManager(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed(self, task_id: str, status: str, files=()) -> Path:
        """直接构造指定状态的任务，跳过真实下载流程。"""
        task = DownloadTask(task_id, "https://example.com/v", "mp4")
        task.status = status
        task_dir = self.root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            (task_dir / name).write_bytes(b"x")
        with self.manager._lock:
            self.manager.tasks[task_id] = task
        return task_dir


class RemoveTaskTest(RemovalTestBase):
    def test_failed_task_removes_record_and_directory(self):
        task_dir = self._seed("a1", "failed", ["yt-dlp.log", "video.mp4.part"])

        result = self.manager.remove_task("a1")

        self.assertTrue(result["removed_files"])
        self.assertIsNone(self.manager.get("a1"))
        self.assertFalse(task_dir.exists())

    def test_cancelled_task_removes_record_and_directory(self):
        task_dir = self._seed("a2", "cancelled", ["video.mp4.part"])

        result = self.manager.remove_task("a2")

        self.assertTrue(result["removed_files"])
        self.assertIsNone(self.manager.get("a2"))
        self.assertFalse(task_dir.exists())

    def test_finished_task_keeps_directory(self):
        task_dir = self._seed("a3", "finished", ["video.mp4"])

        result = self.manager.remove_task("a3")

        self.assertFalse(result["removed_files"])
        self.assertIsNone(self.manager.get("a3"))
        self.assertTrue((task_dir / "video.mp4").is_file())

    def test_rejects_running_task(self):
        task_dir = self._seed("a4", "downloading", ["video.mp4.part"])

        with self.assertRaises(ValueError):
            self.manager.remove_task("a4")

        self.assertIsNotNone(self.manager.get("a4"))
        self.assertTrue(task_dir.exists())

    def test_rejects_queued_task(self):
        self._seed("a5", "queued")

        with self.assertRaises(ValueError):
            self.manager.remove_task("a5")

        self.assertIsNotNone(self.manager.get("a5"))

    def test_rejects_unknown_task(self):
        with self.assertRaises(KeyError):
            self.manager.remove_task("nope")


class RemoveFinishedTest(RemovalTestBase):
    def test_removes_only_ended_tasks(self):
        failed_dir = self._seed("b1", "failed", ["yt-dlp.log"])
        finished_dir = self._seed("b2", "finished", ["video.mp4"])
        running_dir = self._seed("b3", "downloading", ["video.mp4.part"])
        queued_dir = self._seed("b4", "queued")

        removed = self.manager.remove_finished()

        self.assertEqual(removed, 2)
        self.assertIsNone(self.manager.get("b1"))
        self.assertIsNone(self.manager.get("b2"))
        self.assertIsNotNone(self.manager.get("b3"))
        self.assertIsNotNone(self.manager.get("b4"))

        self.assertFalse(failed_dir.exists())     # 失败任务连目录一起清
        self.assertTrue(finished_dir.exists())    # 已完成任务保留成品
        self.assertTrue(running_dir.exists())
        self.assertTrue(queued_dir.exists())

    def test_returns_zero_when_nothing_to_remove(self):
        self._seed("b5", "downloading")

        self.assertEqual(self.manager.remove_finished(), 0)
        self.assertIsNotNone(self.manager.get("b5"))


if __name__ == "__main__":
    unittest.main()
