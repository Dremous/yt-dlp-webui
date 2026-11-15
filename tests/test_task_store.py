"""任务持久化：重启后任务与进度仍在，未完成任务可点重试继续。

不落盘时容器一重启，内存里的任务记录就没了——`_adopt_partial` 找不到「捐赠者」，
上次下到 90% 的 .part 在磁盘上却没人认领，20GB 等于白下。
"""

import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

if "yt_dlp" not in sys.modules:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = object
    sys.modules["yt_dlp"] = _stub

from app.downloader import MAX_TASKS, DownloadManager, DownloadTask
from app.store import TaskStore


def _task(task_id="t1", status="finished", progress=42.0):
    task = DownloadTask(task_id, "https://example.com/v", "best")
    task.status = status
    task.progress = progress
    task.title = "标题"
    task.filename = f"{task_id}/v.mp4"
    return task


class TaskStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "tasks.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip(self):
        store = TaskStore(self.db)
        store.save(_task())
        rows = TaskStore(self.db).load()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], "t1")
        self.assertEqual(row["status"], "finished")
        self.assertEqual(row["progress"], 42.0)
        self.assertEqual(row["title"], "标题")
        self.assertEqual(row["filename"], "t1/v.mp4")

    def test_corrupt_db_is_quarantined_and_rebuilt(self):
        # 断电/磁盘写满后 SQLite 文件可能损坏；它不该让整个 WebUI 起不来
        self.db.write_bytes(b"this is not a sqlite database")
        store = TaskStore(self.db)  # 不应抛异常
        store.save(_task("ok"))
        self.assertEqual(TaskStore(self.db).load()[0]["id"], "ok")
        backups = list(Path(self._tmp.name).glob("tasks.db.corrupt-*"))
        self.assertEqual(len(backups), 1)

    def test_unwritable_path_disables_store_without_crash(self):
        # 配置卷只读、TASKS_DB 指向文件路径等情况下，mkdir/建库都会失败。
        # 这时应降级为「本次运行不持久化」，而不是在 import 期抛 OSError。
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("x", encoding="utf-8")
        store = TaskStore(blocker / "tasks.db")  # 父路径是文件，mkdir 必失败
        self.assertFalse(store.available)
        store.save(_task("x"))
        self.assertEqual(store.load(), [])

    def test_runtime_write_failure_disables_store_without_raising(self):
        """初始化成功之后磁盘写满/被锁：写库失败不能把任务线程直接打死。"""
        store = TaskStore(self.db)
        store.save(_task("before"))
        with mock.patch.object(
            store, "_connect", side_effect=sqlite3.OperationalError("disk I/O error")
        ):
            store.save(_task("during"))

        self.assertFalse(store.available)
        # 失败前已经写入的记录仍然可读；本次运行只是不再尝试持久化。
        self.assertEqual(TaskStore(self.db).load()[0]["id"], "before")

    def test_delete(self):
        store = TaskStore(self.db)
        store.save(_task())
        store.delete("t1")
        self.assertEqual(TaskStore(self.db).load(), [])

    def test_late_persist_does_not_resurrect_removed_task(self):
        """删除任务后，后台线程晚到的 persist 不能把记录重新插回库里。

        删除记录和终态落盘由不同线程执行：remove_task 先删库、后台线程随后
        带着刚结束的状态调用 persist，旧实现会再 INSERT 一次，任务从列表里
        “消失”但在重启后又回来。
        """
        mgr = DownloadManager(Path(self._tmp.name) / "dl", store=TaskStore(self.db))
        task = _task("x", status="finished")
        with mgr._lock:
            mgr.tasks["x"] = task
        mgr.persist(task)
        mgr.remove_task("x")
        mgr.persist(task)
        self.assertEqual(TaskStore(self.db).load(), [])

    def test_restore_marks_interrupted_as_failed(self):
        store = TaskStore(self.db)
        store.save(_task("a", status="downloading", progress=88.0))
        store.save(_task("b", status="queued", progress=0.0))
        store.save(_task("c", status="finished", progress=100.0))

        mgr = DownloadManager(Path(self._tmp.name) / "dl", store=TaskStore(self.db))
        self.assertEqual(mgr.get("a").status, "failed")
        self.assertIn("重启", mgr.get("a").error)
        # 失败就是失败：运行时失败会把进度清零，重启中断也得一致，
        # 否则界面出现「失败 + 87%」这种自相矛盾的状态
        self.assertEqual(mgr.get("a").progress, 0.0)
        row = [r for r in TaskStore(self.db).load() if r["id"] == "a"][0]
        self.assertEqual(row["progress"], 0.0)
        self.assertEqual(mgr.get("b").status, "failed")
        # 已结束的任务原样保留
        self.assertEqual(mgr.get("c").status, "finished")

    def test_restore_keeps_only_newest_rows(self):
        store = TaskStore(self.db)
        for i in range(MAX_TASKS + 5):
            t = _task(f"t{i}", progress=float(i))
            t.created = 1000.0 + i  # 越晚越大
            store.save(t)
        mgr = DownloadManager(Path(self._tmp.name) / "dl", store=TaskStore(self.db))
        self.assertEqual(mgr.count(), MAX_TASKS)
        self.assertIsNone(mgr.get("t0"))  # 最旧的被裁掉

    def test_remove_task_deletes_row(self):
        store = TaskStore(self.db)
        store.save(_task("x", status="finished"))
        mgr = DownloadManager(Path(self._tmp.name) / "dl", store=TaskStore(self.db))
        mgr.remove_task("x")
        self.assertEqual(TaskStore(self.db).load(), [])

    def test_remove_task_keeps_memory_when_store_delete_fails(self):
        """删库失败时不要把内存记录先删掉，否则重启后任务会“复活”。"""
        store = TaskStore(self.db)
        store.save(_task("x", status="finished"))
        mgr = DownloadManager(Path(self._tmp.name) / "dl", store=store)
        task = _task("x", status="finished")
        with mgr._lock:
            mgr.tasks["x"] = task

        with mock.patch.object(
            store, "_connect", side_effect=sqlite3.OperationalError("disk I/O error")
        ):
            with self.assertRaises(ValueError):
                mgr.remove_task("x")

        self.assertIsNotNone(mgr.get("x"), "删库失败时任务应继续留在列表里")
        self.assertFalse(store.available)
        self.assertEqual(TaskStore(self.db).load()[0]["id"], "x")

    def test_hook_flushes_progress(self):
        store = TaskStore(self.db)
        store.save(_task("p", status="downloading", progress=0.0))
        mgr = DownloadManager(Path(self._tmp.name) / "dl", store=TaskStore(self.db))
        task = mgr.get("p")
        mgr._hook({"status": "downloading", "downloaded_bytes": 500,
                   "total_bytes": 1000, "info_dict": {}}, task)
        rows = TaskStore(self.db).load()
        self.assertAlmostEqual(rows[0]["progress"], 50.0, places=1)

    def test_finish_flushes_final_state(self):
        store = TaskStore(self.db)
        store.save(_task("q", status="downloading", progress=10.0))
        mgr = DownloadManager(Path(self._tmp.name) / "dl", store=TaskStore(self.db))
        task = mgr.get("q")
        task.status = "finished"
        task.progress = 100.0
        mgr.persist(task)
        self.assertEqual(TaskStore(self.db).load()[0]["status"], "finished")


if __name__ == "__main__":
    unittest.main()
