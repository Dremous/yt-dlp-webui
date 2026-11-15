"""任务持久化：重启后任务与进度仍在，未完成任务可点重试继续。

不落盘时容器一重启，内存里的任务记录就没了——`_adopt_partial` 找不到「捐赠者」，
上次下到 90% 的 .part 在磁盘上却没人认领，20GB 等于白下。
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

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

    def test_delete(self):
        store = TaskStore(self.db)
        store.save(_task())
        store.delete("t1")
        self.assertEqual(TaskStore(self.db).load(), [])

    def test_restore_marks_interrupted_as_failed(self):
        store = TaskStore(self.db)
        store.save(_task("a", status="downloading", progress=88.0))
        store.save(_task("b", status="queued", progress=0.0))
        store.save(_task("c", status="finished", progress=100.0))

        mgr = DownloadManager(Path(self._tmp.name) / "dl", store=TaskStore(self.db))
        self.assertEqual(mgr.get("a").status, "failed")
        self.assertIn("重启", mgr.get("a").error)
        self.assertAlmostEqual(mgr.get("a").progress, 88.0)
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
