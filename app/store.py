"""任务持久化：SQLite 任务库，以及降级时的墓碑文件。

设计目标：
- 正常路径只读写 SQLite，保证任务在重启后可恢复；
- 数据库不可用时（锁冲突、磁盘只读等）不能让内存任务凭空消失，
  所以删除操作退回追加墓碑文件，`load()` 再按墓碑过滤旧行；
- 墓碑写成功才算删除成功，写失败时上层保留内存记录，避免"界面没了、库还在"。

墓碑文件与数据库同目录，文件名为 `<db>.deleted`，每行一个任务 ID。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from .models import TaskRecord

logger = logging.getLogger(__name__)


class TaskStore:
    def __init__(self, path: str):
        self.path = path
        self.dir = os.path.dirname(path) or "."
        self._lock = threading.RLock()
        self._available = True
        self._disabled_reason = ""
        self._conn: sqlite3.Connection | None = None

    # ---- 基础状态 -------------------------------------------------------
    @property
    def available(self) -> bool:
        return self._available

    @property
    def tombstone_path(self) -> str:
        return f"{self.path}.deleted"

    def _disable(self, exc: BaseException) -> None:
        if self._available:
            self._available = False
            self._disabled_reason = str(exc)
            logger.warning("task store unavailable, falling back to tombstones: %s", exc)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                error TEXT,
                options TEXT NOT NULL DEFAULT '{}',
                filepath TEXT
                )"""
            )
            self._conn.commit()
        yield self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    # ---- 墓碑 -----------------------------------------------------------
    def _read_tombstones(self) -> set[str]:
        path = self.tombstone_path
        if not os.path.exists(path):
            return set()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return {line.strip() for line in fh if line.strip()}
        except OSError as exc:
            logger.warning("read task tombstones failed: %s", exc)
            return set()

    def _append_tombstone(self, task_id: str) -> bool:
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(self.tombstone_path, "a", encoding="utf-8") as fh:
                fh.write(task_id + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return True
        except OSError as exc:
            logger.warning("write task tombstone failed for %s: %s", task_id, exc)
            return False

    def _rewrite_tombstones(self, ids: set[str]) -> bool:
        """整体重写墓碑，用于清理已经确认不存在的 ID；失败时保留旧文件。"""
        path = self.tombstone_path
        tmp = path + ".tmp"
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                for task_id in sorted(ids):
                    fh.write(task_id + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            return True
        except OSError as exc:
            logger.warning("rewrite task tombstones failed: %s", exc)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False

    # ---- 读写 -----------------------------------------------------------
    def load(self) -> list[TaskRecord]:
        with self._lock:
            if not self._available:
                return []
            tombstones = self._read_tombstones()
            try:
                with self._connection() as conn:
                    rows = conn.execute(
                        "SELECT id, url, title, status, created_at, updated_at, error, options, filepath "
                        "FROM tasks ORDER BY created_at"
                    ).fetchall()
            except sqlite3.Error as exc:
                self._disable(exc)
                return []

        records: list[TaskRecord] = []
        for row in rows:
            if row[0] in tombstones:
                continue
            records.append(
                TaskRecord(
                    id=row[0], url=row[1], title=row[2], status=row[3],
                    created_at=row[4], updated_at=row[5], error=row[6],
                    options=row[7], filepath=row[8],
                )
            )

        # 清理已确认不在数据库里的墓碑，避免文件无限增长；清理失败不影响读取结果。
        if tombstones:
            existing = {row[0] for row in rows}
            stale = tombstones - existing
            if stale:
                self._rewrite_tombstones(existing & tombstones)
        return records

    def save(self, record: TaskRecord) -> bool:
        with self._lock:
            if not self._available:
                return False
            try:
                with self._connection() as conn:
                    conn.execute(
                        """INSERT INTO tasks
                        (id, url, title, status, created_at, updated_at, error, options, filepath)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            url=excluded.url,
                            title=excluded.title,
                            status=excluded.status,
                            updated_at=excluded.updated_at,
                            error=excluded.error,
                            options=excluded.options,
                            filepath=excluded.filepath""",
                        (record.id, record.url, record.title, record.status,
                         record.created_at, record.updated_at, record.error,
                         record.options, record.filepath),
                    )
                    conn.commit()
                return True
            except sqlite3.Error as exc:
                self._disable(exc)
                return False

    def delete(self, task_id: str) -> bool:
        """删除任务。

        正常路径删数据库行；数据库不可用或删除抛错时追加墓碑。
        墓碑写成功即返回 True，让上层可以安全移除内存记录；
        只有墓碑也写失败才返回 False。
        """
        with self._lock:
            if self._available:
                try:
                    with self._connection() as conn:
                        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                        conn.commit()
                    return True
                except sqlite3.Error as exc:
                    self._disable(exc)
            return self._append_tombstone(task_id)

    def _delete_once(self, task_id: str) -> bool:
        """供启动裁剪使用的内部删除入口；保留方法便于测试替换。"""
        return self.delete(task_id)
