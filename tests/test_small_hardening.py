import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.downloader import DownloadManager
from app.models import TaskRecord
from app.store import TaskStore


def make_record(task_id: str, created: float | None = None):
    ts = created if created is not None else time.time()
    return TaskRecord(
        id=task_id,
        url='https://example.com/video',
        title='title',
        status='finished',
        created_at=ts,
        updated_at=ts,
        error=None,
        options='{}',
        filepath=None,
    )


class SaveProxyConcurrencyTest(unittest.TestCase):
    def test_concurrent_proxy_saves_keep_disk_and_memory_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            proxy_file = Path(tmp) / 'proxy.txt'
            manager = DownloadManager(Path(tmp) / 'downloads', proxy_file=str(proxy_file))
            first_writing = threading.Event()
            release_first = threading.Event()
            errors = []
            original = manager._atomic_write

            def slow_write(path, data, *, binary):
                # 第一个线程进入写盘窗口后暂停，让第二个线程尝试保存
                if not first_writing.is_set():
                    first_writing.set()
                    release_first.wait(timeout=2)
                return original(path, data, *, binary=binary)

            def save(url):
                try:
                    manager.save_proxy(url)
                except Exception as exc:
                    errors.append(exc)

            with patch.object(manager, '_atomic_write', side_effect=slow_write):
                t1 = threading.Thread(target=save, args=('http://127.0.0.1:1111',))
                t2 = threading.Thread(target=save, args=('http://127.0.0.1:2222',))
                t1.start()
                self.assertTrue(first_writing.wait(timeout=2))
                t2.start()
                # 第二个线程必须被锁挡在写盘窗口外，不能先写盘再更新内存
                time.sleep(0.1)
                release_first.set()
                t1.join(timeout=2)
                t2.join(timeout=2)

            self.assertEqual(errors, [])
            disk = proxy_file.read_text(encoding='utf-8').strip()
            self.assertEqual(disk, manager.proxy)
            manager.remove_proxy()
            self.assertFalse(proxy_file.exists())
            self.assertIsNone(manager.proxy)


class RemoveFinishedPersistenceTest(unittest.TestCase):
    def test_remove_finished_when_store_disabled_still_removes_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            manager = DownloadManager(Path(tmp) / 'downloads', store=store)
            store.save(make_record('a'))
            store.save(make_record('b'))
            manager._restore_tasks()
            # 让 DB 不可用，但墓碑仍可写：删除必须继续走墓碑而不是跳过
            with patch.object(store, '_connection', side_effect=sqlite3.OperationalError('db down')):
                self.assertEqual(manager.remove_finished(), 2)
            self.assertEqual(manager.count(), 0)
            self.assertTrue(Path(db + '.deleted').exists())
            manager2 = DownloadManager(Path(tmp) / 'downloads', store=TaskStore(db))
            self.assertEqual(manager2.count(), 0)

    def test_remove_task_uses_tombstone_when_db_delete_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            manager = DownloadManager(Path(tmp) / 'downloads', store=store)
            store.save(make_record('a'))
            manager._restore_tasks()
            with patch.object(store, '_connection', side_effect=sqlite3.OperationalError('db down')):
                result = manager.remove_task('a')
            self.assertEqual(result['id'], 'a')
            self.assertEqual(manager.count(), 0)
            manager2 = DownloadManager(Path(tmp) / 'downloads', store=TaskStore(db))
            self.assertEqual(manager2.count(), 0)

    def test_restore_caps_memory_when_trim_delete_falls_back_to_tombstone(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            for i in range(51):
                store.save(make_record(f't{i}', created=1000 + i))
            manager = DownloadManager(Path(tmp) / 'downloads', store=store)
            # 模拟启动裁剪时第一条删除遇到 I/O 错误，但墓碑可写
            real_delete = store.delete
            first = {'done': False}

            def delete_with_one_failure(task_id):
                if not first['done'] and task_id == 't0':
                    first['done'] = True
                    with patch.object(store, '_connection', side_effect=sqlite3.OperationalError('db down')):
                        return real_delete(task_id)
                return real_delete(task_id)

            with patch.object(store, 'delete', side_effect=delete_with_one_failure):
                manager._restore_tasks()
            self.assertEqual(manager.count(), 50)
            self.assertIsNone(manager.get('t0'))
            manager2 = DownloadManager(Path(tmp) / 'downloads', store=TaskStore(db))
            self.assertEqual(manager2.count(), 50)
            self.assertIsNone(manager2.get('t0'))

    def test_restore_keeps_rows_when_tombstone_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            for i in range(51):
                store.save(make_record(f't{i}', created=1000 + i))
            manager = DownloadManager(Path(tmp) / 'downloads', store=store)

            with patch.object(store, '_connect', side_effect=sqlite3.OperationalError('db down')), \
                    patch.object(store, '_append_tombstone', return_value=False):
                manager._restore_tasks()
            # 极端场景：库和墓碑都不可写。保留旧行比静默丢任务更安全，
            # 代价是内存可以短暂超过 MAX_TASKS（测试锁定这个取舍）。
            self.assertEqual(manager.count(), 51)
