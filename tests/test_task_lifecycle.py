"""任务删除行为的单元测试。

本地不装 yt-dlp，用最小 stub 顶替；这里只验证任务字典与磁盘目录的清理策略，
不涉及真实网络下载。

清理规则（与界面的确认文案一致）：
- failed / cancelled：目录里只有 .part、.log 等残留，连目录一起删
- finished：视频文件还有用，只删记录、保留目录
- downloading / queued：必须先取消，拒绝删除
"""

import contextlib
import io
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
from app.tasklog import TaskLogger


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


class CountEndedTest(RemovalTestBase):
    def test_counts_only_ended_states(self):
        """「清理已结束」按钮要用全量计数，不能只数列表里可见的那几条。"""
        self._seed("c1", "finished")
        self._seed("c2", "failed")
        self._seed("c3", "cancelled")
        self._seed("c4", "downloading")
        self._seed("c5", "queued")

        self.assertEqual(self.manager.count_ended(), 3)

    def test_returns_zero_when_all_running(self):
        self._seed("c6", "downloading")

        self.assertEqual(self.manager.count_ended(), 0)


class ReportFailureTest(RemovalTestBase):
    def test_skips_removed_task(self):
        """任务记录已被删除时，失败处理不能再把目录建回来（否则留下孤儿目录）。"""
        task = DownloadTask("d1", "https://example.com/v", "mp4")
        task.status = "failed"
        # 刻意不创建目录：模拟刚被 remove_task 的 rmtree 清掉
        task_dir = self.root / "d1"
        logger = TaskLogger()
        logger.error("ERROR: boom")

        summary = self.manager._report_failure(
            task, task_dir, logger, RuntimeError("x")
        )

        self.assertFalse(task_dir.exists(), "已删除任务的目录不应被失败处理重建")
        self.assertNotIn("完整日志", summary)

    def test_removes_directory_when_task_deleted_during_write(self):
        """写盘期间任务被删：刚写进去的日志目录必须一并回收。

        覆盖的是「检查通过 → mkdir 写盘 → remove_task 的 rmtree 先执行」这个窗口，
        用替换 write 的方式精确卡在写盘完成的那一刻删除任务记录。
        """
        task = DownloadTask("d3", "https://example.com/v", "mp4")
        task.status = "failed"
        task_dir = self.root / "d3"
        task_dir.mkdir(parents=True)
        with self.manager._lock:
            self.manager.tasks["d3"] = task

        logger = TaskLogger()
        logger.error("ERROR: boom")

        original_write = logger.write

        def write_then_delete(path):
            original_write(path)  # 先真的落盘
            with self.manager._lock:
                self.manager.tasks.pop("d3", None)  # 模拟并发删除

        logger.write = write_then_delete

        summary = self.manager._report_failure(
            task, task_dir, logger, RuntimeError("x")
        )

        self.assertFalse(task_dir.exists(), "写盘期间被删的任务不应留下孤儿目录")
        self.assertNotIn("完整日志", summary)

    def test_writes_log_for_live_task(self):
        """任务还在列表里时，失败现场照常落盘。"""
        task = DownloadTask("d2", "https://example.com/v", "mp4")
        task.status = "failed"
        task_dir = self.root / "d2"
        task_dir.mkdir(parents=True)
        with self.manager._lock:
            self.manager.tasks["d2"] = task
        logger = TaskLogger()
        logger.error("ERROR: boom")

        # _report_failure 会把日志末段打到 stderr，测试里吞掉，免得污染 unittest 输出
        with contextlib.redirect_stderr(io.StringIO()):
            summary = self.manager._report_failure(
                task, task_dir, logger, RuntimeError("x")
            )

        self.assertTrue((task_dir / "yt-dlp.log").is_file())
        self.assertIn("完整日志", summary)


if __name__ == "__main__":
    unittest.main()
