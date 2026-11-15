"""任务记录的持久化（SQLite）。

为什么需要：任务列表原本只在内存里（`DownloadManager.tasks`），容器一重启就清空。
而 `_adopt_partial` 是「从已结束的历史任务里把 .part 接管过来」——记录没了，它就
找不到捐赠者，磁盘上下了 90% 的残片没人认领，等于白下。

为什么用 SQLite：标准库自带、单文件、无需额外服务；每行一次 UPSERT，写放大可以
忽略（最多 50 行，进度按 1% 节流写入）。放在配置卷而不是下载卷：任务表体积固定且
很小，配置卷本来就是「小、要持久」的定位，下载卷则可能塞满几十 GB 的成品。
"""

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

# 落盘的字段：速度 / 剩余时间是瞬时值，重启后必然过时，刻意不存
_COLUMNS = (
    "id", "url", "format", "status", "progress",
    "stage", "title", "filename", "error", "created",
)


def _row_to_dict(row: sqlite3.Row) -> Dict:
    return {name: row[name] for name in _COLUMNS}


class TaskStore:
    """任务记录的读写。线程安全：每次调用开独立连接，写操作加锁串行。

    路径不可用（配置卷只读、父路径是文件、磁盘满）时不会让进程崩掉：先尝试
    隔离坏库重建；重建仍失败就降级为「本次运行不持久化」，WebUI 照常可用。
    """

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._disabled = False
        self._init_or_disable()

    @property
    def available(self) -> bool:
        """当前是否真正在持久化（降级模式下 save/delete 是空操作）。"""
        return not self._disabled

    def _init_or_disable(self) -> None:
        try:
            self._prepare_and_init()
            return
        except (sqlite3.DatabaseError, OSError) as exc:
            # 断电 / 磁盘写满后文件可能损坏。直接抛会让 import 阶段崩掉，
            # 容器在 restart: unless-stopped 下无限重启，WebUI 完全打不开。
            backup = self._try_quarantine(exc)
            try:
                self._prepare_and_init()
            except (sqlite3.DatabaseError, OSError) as retry_exc:
                self._disabled = True
                log = logging.getLogger(__name__)
                if backup:
                    log.warning(
                        "任务库损坏（%s），已备份为 %s；重建失败，本次运行不持久化任务：%s",
                        exc, backup.name, retry_exc,
                    )
                else:
                    log.warning(
                        "任务库不可用（%s），本次运行不持久化任务：%s",
                        self.path, retry_exc,
                    )
                return
            if backup:
                logging.getLogger(__name__).warning(
                    "任务库损坏（%s），已备份为 %s 并重建", exc, backup.name
                )

    def _prepare_and_init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _try_quarantine(self, exc: Exception) -> Optional[Path]:
        if not self.path.exists():
            return None
        try:
            return self._quarantine()
        except OSError as qexc:
            logging.getLogger(__name__).warning(
                "任务库损坏且无法隔离（%s）：%s", exc, qexc
            )
            return None

    def _quarantine(self) -> Path:
        """把不可用的库文件改名留证；挪不动时抛 OSError（磁盘/权限问题另说）。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        index = 1
        while backup.exists():
            backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}-{index}")
            index += 1
        os.replace(self.path, backup)
        return backup

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

    def _disable(self, exc: Exception) -> None:
        """初始化成功之后又遇到磁盘写满/锁库：本次运行停止持久化，但进程继续。"""
        if self._disabled:
            return
        self._disabled = True
        logging.getLogger(__name__).warning(
            "任务库运行时不可用，本次运行停止持久化任务：%s", exc
        )

    def save(self, task) -> None:
        """写入或更新一条任务记录（按 id UPSERT）。"""
        if self._disabled:
            return
        values = tuple(getattr(task, name, None) for name in _COLUMNS)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(f"{name} = excluded.{name}" for name in _COLUMNS if name != "id")
        sql = (
            f"INSERT INTO tasks ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(sql, values)
                    conn.commit()
                finally:
                    conn.close()
        except (sqlite3.DatabaseError, OSError) as exc:
            # 旧实现会让异常穿透后台下载线程，任务永远停在 downloading。
            self._disable(exc)

    def delete(self, task_id: str) -> bool:
        if self._disabled:
            return False
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                    conn.commit()
                finally:
                    conn.close()
        except (sqlite3.DatabaseError, OSError) as exc:
            self._disable(exc)
            return False
        return True

    def load(self) -> List[Dict]:
        """按创建时间升序返回全部记录（恢复时取尾部的最新若干条）。"""
        if self._disabled:
            return []
        try:
            with self._lock:
                conn = self._connect()
                try:
                    rows = conn.execute(
                        f"SELECT {', '.join(_COLUMNS)} FROM tasks ORDER BY created"
                    ).fetchall()
                finally:
                    conn.close()
        except (sqlite3.DatabaseError, OSError) as exc:
            # 启动恢复阶段读库失败也不能让 import 崩掉；按空任务列表继续。
            self._disable(exc)
            return []
        return [_row_to_dict(row) for row in rows]
