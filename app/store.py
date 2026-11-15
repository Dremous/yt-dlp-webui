"""任务记录的持久化（SQLite）。

为什么需要：任务列表原本只在内存里（`DownloadManager.tasks`），容器一重启就清空。
而 `_adopt_partial` 是「从已结束的历史任务里把 .part 接管过来」——记录没了，它就
找不到捐赠者，磁盘上下了 90% 的残片没人认领，等于白下。

为什么用 SQLite：标准库自带、单文件、无需额外服务；每行一次 UPSERT，写放大可以
忽略（最多 50 行，进度按 1% 节流写入）。放在配置卷而不是下载卷：任务表体积固定且
很小，配置卷本来就是「小、要持久」的定位，下载卷则可能塞满几十 GB 的成品。
"""

import sqlite3
import threading
from pathlib import Path
from typing import Dict, List

# 落盘的字段：速度 / 剩余时间是瞬时值，重启后必然过时，刻意不存
_COLUMNS = (
    "id", "url", "format", "status", "progress",
    "stage", "title", "filename", "error", "created",
)


def _row_to_dict(row: sqlite3.Row) -> Dict:
    return {name: row[name] for name in _COLUMNS}


class TaskStore:
    """任务记录的读写。线程安全：每次调用开独立连接，写操作加锁串行。"""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        url TEXT NOT NULL,
                        format TEXT NOT NULL,
                        status TEXT NOT NULL,
                        progress REAL NOT NULL DEFAULT 0,
                        stage TEXT,
                        title TEXT,
                        filename TEXT,
                        error TEXT,
                        created REAL NOT NULL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def save(self, task) -> None:
        """写入或更新一条任务记录（按 id UPSERT）。"""
        values = tuple(getattr(task, name, None) for name in _COLUMNS)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(f"{name} = excluded.{name}" for name in _COLUMNS if name != "id")
        sql = (
            f"INSERT INTO tasks ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, values)
                conn.commit()
            finally:
                conn.close()

    def delete(self, task_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.commit()
            finally:
                conn.close()

    def load(self) -> List[Dict]:
        """按创建时间升序返回全部记录（恢复时取尾部的最新若干条）。"""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM tasks ORDER BY created"
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_dict(row) for row in rows]
