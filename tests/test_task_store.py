import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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


class TaskStoreTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            rec = make_record('abc')
            self.assertTrue(store.save(rec))
            loaded = store.load()
            self.assertEqual([r.id for r in loaded], ['abc'])
            self.assertEqual(loaded[0].url, rec.url)
            store.close()

    def test_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            store.save(make_record('abc'))
            store.save(make_record('def'))
            self.assertTrue(store.delete('abc'))
            self.assertEqual([r.id for r in store.load()], ['def'])
            store.close()

    def test_delete_when_db_unavailable_writes_tombstone(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            store.save(make_record('abc'))
            # 模拟数据库文件被外部删除/损坏，delete 会失败并进入降级
            with patch.object(store, '_connect', side_effect=sqlite3.OperationalError('db down')):
                self.assertTrue(store.delete('abc'))
            self.assertTrue(store.available is False)
            self.assertTrue(Path(db + '.deleted').exists())
            store.close()

    def test_load_filters_tombstones_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            store.save(make_record('abc'))
            store.save(make_record('def'))
            with patch.object(store, '_connect', side_effect=sqlite3.OperationalError('db down')):
                self.assertTrue(store.delete('abc'))
            store.close()

            # 新进程重新打开同一个目录：旧行还在，但墓碑必须把它过滤掉
            reopened = TaskStore(db)
            self.assertEqual([r.id for r in reopened.load()], ['def'])
            reopened.close()

    def test_delete_falls_back_to_tombstone_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            store.save(make_record('abc'))
            with patch.object(store, '_connection', side_effect=sqlite3.OperationalError('db down')):
                self.assertTrue(store.delete('abc'))
            store.close()
            reopened = TaskStore(db)
            self.assertEqual(reopened.load(), [])
            reopened.close()

    def test_delete_returns_false_when_tombstone_also_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            store.save(make_record('abc'))
            with patch.object(store, '_connection', side_effect=sqlite3.OperationalError('db down')), \
                    patch.object(store, '_append_tombstone', return_value=False):
                self.assertFalse(store.delete('abc'))
            store.close()

    def test_tombstones_are_cleaned_when_db_rows_are_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            store.save(make_record('abc'))
            with patch.object(store, '_connection', side_effect=sqlite3.OperationalError('db down')):
                self.assertTrue(store.delete('abc'))
            store.close()
            # 手工模拟数据库行已经被别处清理；墓碑应在下次 load 时被清掉
            conn = sqlite3.connect(db)
            conn.execute('DELETE FROM tasks')
            conn.commit()
            conn.close()
            reopened = TaskStore(db)
            self.assertEqual(reopened.load(), [])
            self.assertFalse(Path(db + '.deleted').exists())
            reopened.close()


class TaskStoreConcurrencyTest(unittest.TestCase):
    def test_save_and_delete_are_serialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, 'tasks.db')
            store = TaskStore(db)
            stop = threading.Event()
            errors = []

            def writer():
                i = 0
                while not stop.is_set():
                    try:
                        store.save(make_record(f'w{i}'))
                        i += 1
                    except Exception as exc:
                        errors.append(exc)

            def deleter():
                while not stop.is_set():
                    try:
                        store.delete('w0')
                    except Exception as exc:
                        errors.append(exc)

            threads = [threading.Thread(target=writer), threading.Thread(target=deleter)]
            for t in threads:
                t.start()
            time.sleep(0.2)
            stop.set()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            # 不要求具体条数，只要求没有抛异常且数据库仍然可读
            self.assertIsInstance(store.load(), list)
            store.close()
