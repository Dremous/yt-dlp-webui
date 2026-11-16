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
from typing import Dict, List, Optional, Set

# 落盘的字段：速度 / 剩余时间是瞬时值，重启后必然过时，刻意不存
_COLUMNS = (
    "id", "url", "format", "status", "progress",
    "stage", "title", "filename", "error", "created",
)

# SQLite 的“锁库”是暂时状态：别的连接可能正在提交，等一会儿通常能成功。
# 不能和磁盘满 / 文件损坏一样直接永久降级，否则一次瞬时锁库会让本次运行
# 剩下的任务全部不落盘。
_MAX_LOCK_RETRIES = 3
_LOCK_RETRY_SLEEP = 0.05


def _is_transient_lock(exc: Exception) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _row_to_dict(row: sqlite3.Row) -> Dict:
    return {name: row[name] for name in _COLUMNS}


class TaskStore:
    """任务记录的读写。线程安全：每次调用开独立连接，写操作加锁串行。

    路径不可用（配置卷只读、父路径是文件、磁盘满）时不会让进程崩掉：先尝试
    隔离坏库重建；重建仍失败就降级为「本次运行不再落盘新状态」，WebUI 照常可用。
    删除操作例外：降级时仍会尝试写 `<db>.deleted` 墓碑，避免“界面删了、重启复活”。
    """

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._disabled = False
        self._deleted_ids: Set[str] = self._read_deleted()
        self._init_or_disable()

    @property
    def _tombstone_path(self) -> Path:
        """删除墓碑文件：库不可写时也能记录“这条任务已逻辑删除”。

        放在任务库旁边（配置卷），和 tasks.db 一起持久化；启动时 load 会过滤
        这些 ID，避免降级模式下删了内存、重启后旧行又冒出来。
        """
        return self.path.with_name(f"{self.path.name}.deleted")

    def _read_deleted(self) -> Set[str]:
        try:
            text = self._tombstone_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set()
        return {line.strip() for line in text.splitlines() if line.strip()}

    def _record_deleted(self, task_id: str) -> bool:
        """追加一条墓碑；失败返回 False，让调用方保留内存记录。"""
        if not task_id or "\n" in task_id or "\r" in task_id:
            return False
        with self._lock:
            try:
                with self._tombstone_path.open("a", encoding="utf-8") as fh:
                    fh.write(task_id + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError:
                return False
            self._deleted_ids.add(task_id)
        return True

    @property
    def available(self) -> bool:
        """当前是否能正常读写任务库（降级时 save 空操作，delete 退回墓碑）。"""
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

    def _with_retry(self, operation):
        """执行一次 SQLite 操作；只对临时的 locked/busy 做有限重试。"""
        for attempt in range(_MAX_LOCK_RETRIES):
            try:
                return operation()
            except (sqlite3.DatabaseError, OSError) as exc:
                if attempt == _MAX_LOCK_RETRIES - 1 or not _is_transient_lock(exc):
                    raise
                time.sleep(_LOCK_RETRY_SLEEP * (attempt + 1))

    def _save_once(self, sql: str, values: tuple) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(sql, values)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def task_values(task) -> tuple:
        """把任务对象转成落盘字段值，供调用方在锁外安全地写库。"""
        return tuple(getattr(task, name, None) for name in _COLUMNS)

    def save(self, task) -> None:
        """写入或更新一条任务记录（按 id UPSERT）。"""
        self.save_values(self.task_values(task))

    def save_values(self, values: tuple) -> None:
        """写入一条已经快照好的任务字段值。

        调用方可以先在任务锁内复制 values，再在锁外调用本方法，避免把 SQLite
        的磁盘等待带进任务状态锁。
        """
        if self._disabled:
            return
        placeholders = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(f"{name} = excluded.{name}" for name in _COLUMNS if name != "id")
        sql = (
            f"INSERT INTO tasks ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
        )
        try:
            self._with_retry(lambda: self._save_once(sql, values))
        except (sqlite3.DatabaseError, OSError) as exc:
            # 旧实现会让异常穿透后台下载线程，任务永远停在 downloading。
            self._disable(exc)

    def _delete_once(self, task_id: str) -> bool:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.commit()
            finally:
                conn.close()
        return True

    def delete(self, task_id: str) -> bool:
        """删除一行；库不可写时退回墓碑，保证删除结果在重启后仍然有效。"""
        if self._disabled:
            return self._record_deleted(task_id)
        try:
            return self._with_retry(lambda: self._delete_once(task_id))
        except (sqlite3.DatabaseError, OSError) as exc:
            self._disable(exc)
            # 库删不掉但配置卷还可写时，墓碑是唯一能跨重启的删除证据。
            # 墓碑也写不进去才返回 False，调用方会保留内存记录并提示失败。
            return self._record_deleted(task_id)

    def _load_once(self) -> List[Dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM tasks ORDER BY created"
                ).fetchall()
            finally:
                conn.close()
        return [_row_to_dict(row) for row in rows]

    def load(self) -> List[Dict]:
        """按创建时间升序返回全部记录（恢复时取尾部的最新若干条）。"""
        if self._disabled:
            return []
        try:
            rows = self._with_retry(self._load_once)
        except (sqlite3.DatabaseError, OSError) as exc:
            # 启动恢复阶段读库失败也不能让 import 崩掉；按空任务列表继续。
            self._disable(exc)
            return []
        deleted = self._read_deleted()
        return [row for row in rows if row["id"] not in deleted]
