"""下载管理器：基于 yt-dlp 的 Python API 实现后台下载任务。

设计要点：
- 所有 URL 都通过 yt-dlp 的 Python API 传递（绝不使用 shell 拼接），避免命令注入。
- 每个任务写入自己的子目录，产物按任务隔离，消除并发误判。
- 用信号量限制最大并发，用上限清理完成任务，防止线程/内存无限增长。
- Cookie 与代理都以「文件持久化 + 内存缓存」的方式提供，改完立即对新任务生效。
"""

import os
import shutil
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import yt_dlp

# ---------------------------------------------------------------------------
# 输出格式预设
# ---------------------------------------------------------------------------
FORMATS: Dict[str, dict] = {
    "mp4": {
        "label": "MP4 视频（最高 mp4 画质）",
        "options": {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
        },
    },
    "best": {
        "label": "最佳画质（保留原始格式）",
        "options": {
            "format": "bestvideo+bestaudio/best",
        },
    },
    "mp3": {
        "label": "MP3 音频（192kbps）",
        "options": {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        },
    },
}

# 资源上限（防止无限增长 / 无节制并发）
MAX_CONCURRENT = 5     # 最大并发下载数
MAX_TASKS = 50         # 内存中保留的最大任务记录数（正常手工下载几乎不会触顶，仅防脚本刷任务）

# 下载中可能残留的临时/中间文件后缀，不视作最终产物（列表扫描时同样过滤）
TMP_SUFFIXES = (
    ".part", ".ytdl", ".tmp", ".temp", ".webp",
    ".jpg", ".png", ".description", ".info.json",
)

# 允许的代理协议：socks5h 表示由代理端解析域名，容器/远程代理场景更省事
ALLOWED_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")


def normalize_proxy(url: str) -> str:
    """校验并规范化代理地址；非法时抛出 ValueError（中文提示，直接反馈给前端）。

    允许省略协议，如只填 `host.docker.internal:7890`，缺省按 http:// 处理。
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("代理地址不能为空")
    if "://" not in raw:
        raw = "http://" + raw  # 只填 host:port 时按 http 代理处理
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ALLOWED_PROXY_SCHEMES:
        raise ValueError("代理协议仅支持 http / https / socks5 / socks5h")
    if not parsed.hostname:
        raise ValueError("代理地址缺少主机名")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("代理端口不合法（应为 0-65535 的整数）")
    if port is None:
        raise ValueError("代理地址必须带端口，例如 http://host.docker.internal:7890")
    return raw


class DownloadCancelled(Exception):
    """下载被用户主动取消（或排队超限被清理）时抛出的内部信号。"""

    pass


class DownloadTask:
    """单个下载任务的状态。"""

    def __init__(self, task_id: str, url: str, fmt: str):
        self.id = task_id
        self.url = url
        self.format = fmt
        self.status = "queued"          # queued / downloading / finished / failed
        self.progress: float = 0.0      # 0 ~ 100
        self.speed: Optional[str] = None
        self.eta: Optional[str] = None
        self.title: Optional[str] = None
        self.filename: Optional[str] = None  # 相对下载目录的路径（含任务子目录前缀）
        self.error: Optional[str] = None
        self.created: float = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "format": self.format,
            "status": self.status,
            "progress": round(self.progress, 1),
            "speed": self.speed,
            "eta": self.eta,
            "title": self.title,
            "filename": self.filename,
            "error": self.error,
            "created": self.created,
        }


class DownloadManager:
    """管理并发下载任务，每个任务跑在独立后台线程中。

    cookie_file：可选。指向 Netscape 格式的 cookies.txt，
    若文件存在则自动附加到 yt-dlp 的 cookiefile 选项，用于登录/绕过限制。
    proxy_file：可选。指向保存代理地址的文本文件（一行 URL），
    若存在则让 yt-dlp 的出站请求走该代理，用于国内访问 YouTube 等被墙站点。
    """

    def __init__(self, download_dir: Path, cookie_file: Optional[str] = None,
                 proxy_file: Optional[str] = None):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_file = Path(cookie_file) if cookie_file else None
        self.proxy_file = Path(proxy_file) if proxy_file else None
        self.tasks: Dict[str, DownloadTask] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)
        self._proxy: Optional[str] = None  # 当前生效的代理地址（None = 直连）
        self._load_proxy()

    # ------------------------------------------------------------------
    # Cookie 支持
    # ------------------------------------------------------------------
    def cookie_ready(self) -> bool:
        """是否存在可用的 Cookie 文件。"""
        return self.cookie_file is not None and self.cookie_file.is_file()

    def save_cookie(self, data: bytes) -> None:
        """保存 Cookie 文件内容（供后端上传接口调用），并收紧文件权限。

        采用「临时文件 + 原子替换」写入，避免并发下载读取到写了一半的 cookie。
        """
        if self.cookie_file is None:
            raise ValueError("未配置 COOKIE_FILE，无法保存 Cookie")
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cookie_file.with_name(self.cookie_file.name + ".tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o600)  # Cookie 含敏感登录凭证，限制为本用户可读写
        os.replace(tmp, self.cookie_file)  # 原子替换，读者不会读到半截内容

    def remove_cookie(self) -> None:
        """删除已保存的 Cookie 文件。"""
        if self.cookie_file is not None and self.cookie_file.exists():
            self.cookie_file.unlink()

    def cookie_info(self) -> dict:
        """返回 Cookie 状态信息。"""
        if not self.cookie_ready():
            return {"active": False}
        return {
            "active": True,
            "name": self.cookie_file.name,
            "updated": int(self.cookie_file.stat().st_mtime),
            "size": self.cookie_file.stat().st_size,
        }

    # ------------------------------------------------------------------
    # 代理支持
    # ------------------------------------------------------------------
    @property
    def proxy(self) -> Optional[str]:
        """当前生效的代理地址（None 表示直连）。读时加锁，避免与保存操作竞争。"""
        with self._lock:
            return self._proxy

    def proxy_status(self) -> dict:
        """返回代理状态，供前端展示。"""
        proxy = self.proxy
        if not proxy:
            return {"active": False, "url": None}
        return {"active": True, "url": proxy}

    def save_proxy(self, url: str) -> str:
        """保存代理地址并立即生效，返回规范化后的地址。

        与 save_cookie 同策略：先校验再「临时文件 + 原子替换」写入，
        避免并发下载读到写了一半的内容；代理常含账号密码，权限收为 0600。
        """
        if self.proxy_file is None:
            raise ValueError("未配置 PROXY_FILE，无法保存代理")
        proxy = normalize_proxy(url)  # 非法地址在落盘前就拒绝
        self.proxy_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.proxy_file.with_name(self.proxy_file.name + ".tmp")
        tmp.write_text(proxy + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)  # 代理地址可能含 user:password，限制为本用户可读写
        os.replace(tmp, self.proxy_file)
        with self._lock:
            self._proxy = proxy  # 立即对后续任务生效，无需重启容器
        return proxy

    def remove_proxy(self) -> None:
        """删除已保存的代理配置并恢复直连。"""
        if self.proxy_file is not None and self.proxy_file.exists():
            self.proxy_file.unlink()
        with self._lock:
            self._proxy = None

    def _load_proxy(self) -> None:
        """启动时从磁盘恢复代理配置（容器重启后依然生效）。"""
        if self.proxy_file is None or not self.proxy_file.is_file():
            return
        try:
            raw = self.proxy_file.read_text(encoding="utf-8").strip()
        except OSError:
            return  # 文件不可读时按未配置处理，不阻塞服务启动
        if not raw:
            return
        try:
            self._proxy = normalize_proxy(raw)
        except ValueError:
            self._proxy = None  # 手工改坏的文件忽略掉，视为未配置

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def submit(self, url: str, fmt: str) -> DownloadTask:
        task = DownloadTask(uuid.uuid4().hex[:8], url, fmt)
        with self._lock:
            self._trim_tasks()
            self.tasks[task.id] = task
        threading.Thread(target=self._run, args=(task,), daemon=True).start()
        return task

    def snapshot(self, limit: Optional[int] = None) -> List[dict]:
        """返回所有任务的可序列化快照（按创建时间倒序）。limit 可限制返回条数。"""
        with self._lock:
            items = [t.to_dict() for t in self.tasks.values()]
        items.sort(key=lambda d: d["created"], reverse=True)
        if limit is not None:
            items = items[:limit]
        return items

    def get(self, task_id: str) -> Optional[DownloadTask]:
        with self._lock:
            return self.tasks.get(task_id)

    def count(self) -> int:
        """当前内存中的任务总数（含已结束任务）。"""
        with self._lock:
            return len(self.tasks)

    # ------------------------------------------------------------------
    # 内部执行
    # ------------------------------------------------------------------
    def _run(self, task: DownloadTask) -> None:
        # 用信号量限制最大并发；排队任务在此等待空闲并发位
        self._slots.acquire()
        try:
            # 若排队期间被取消/清理，acquire 到后直接返回，避免孤儿下载
            if task.status == "cancelled":
                return
            self._download(task)
        finally:
            self._slots.release()

    def _download(self, task: DownloadTask) -> None:
        # 每个任务写入自己的子目录，产物互不干扰
        task_dir = self.download_dir / task.id
        task_dir.mkdir(parents=True, exist_ok=True)

        task.status = "downloading"
        opts = {
            "outtmpl": str(task_dir / "%(title).100B [%(id)s].%(ext)s"),
            "noplaylist": True,          # 只下载单个视频，不展开播放列表
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [lambda d: self._hook(d, task)],
            # 后期阶段（转码/合并）也会回调，保证取消在 postprocessing 也能生效
            "postprocessor_hooks": [lambda d: self._hook(d, task)],
            **FORMATS[task.format]["options"],
        }

        # 若已导入 Cookie，则附加 cookiefile（用于登录站点/绕过限制）
        if self.cookie_ready():
            opts["cookiefile"] = str(self.cookie_file)

        # 若已配置代理，则让 yt-dlp 的所有出站请求走代理（国内访问 YouTube 必需）
        proxy = self.proxy
        if proxy:
            opts["proxy"] = proxy

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(task.url, download=True)
            task.title = (info or {}).get("title")
            task.filename = self._task_output(task_dir)
            task.status = "finished"
            task.progress = 100.0
            task.speed = None
            task.eta = None
        except DownloadCancelled:
            # 用户主动取消：清空进度，标记已取消，并清理任务目录（含 .part 残留）
            task.status = "cancelled"
            task.speed = None
            task.eta = None
            task.progress = 0.0
            shutil.rmtree(task_dir, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 - 需要把任意下载异常反馈给前端
            task.status = "failed"
            task.error = str(exc)[:500]
            task.speed = None
            task.eta = None
            task.progress = 0.0
            # 失败：仅清理空目录（保留可能的中间文件供排查/重试）
            try:
                if not any(task_dir.iterdir()):
                    task_dir.rmdir()
            except OSError:
                pass

    def _hook(self, data: dict, task: DownloadTask) -> None:
        # 取消信号：一旦任务被标记为取消，立即终止 yt-dlp 下载
        if task.status == "cancelled":
            raise DownloadCancelled()
        # progress / postprocessor 回调结构不同，用 .get 防御缺键
        if data.get("status") == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes", 0)
            if total:
                task.progress = min(downloaded / total * 100.0, 100.0)
            task.speed = data.get("_speed_str") or data.get("speed_str")
            task.eta = data.get("_eta_str") or data.get("eta_str")

    # ------------------------------------------------------------------
    # 产物定位 & 任务清理
    # ------------------------------------------------------------------
    # 下载中可能残留的临时/中间文件后缀，不视作最终产物
    def _task_output(self, task_dir: Path) -> Optional[str]:
        """返回任务子目录中最新的成品文件（排除临时/中间文件），相对下载目录的路径。"""
        files = [
            p for p in task_dir.iterdir()
            if p.is_file() and not p.name.endswith(TMP_SUFFIXES)
        ]
        if not files:
            return None
        newest = max(files, key=lambda p: p.stat().st_mtime)
        return newest.relative_to(self.download_dir).as_posix()

    def _trim_tasks(self) -> None:
        """任务数超上限时清理：先清已完成/失败/已取消，仍超再取消并清最老的排队任务。"""
        if len(self.tasks) < MAX_TASKS:
            return
        # 第一优先：清理已完成/失败/已取消的最老任务（cancelled 的线程已自查返回，删除安全）
        inactive = [
            (k, self.tasks[k].created)
            for k in self.tasks
            if self.tasks[k].status in ("finished", "failed", "cancelled")
        ]
        inactive.sort(key=lambda x: x[1])
        for k, _ in inactive:
            del self.tasks[k]
            if len(self.tasks) < MAX_TASKS:
                return
        # 仍超限：取消并清理最老的排队任务（线程 acquire 后自查返回，不产生孤儿下载）
        queued = [
            (k, self.tasks[k].created)
            for k in self.tasks
            if self.tasks[k].status == "queued"
        ]
        queued.sort(key=lambda x: x[1])
        for k, _ in queued:
            self.tasks[k].status = "cancelled"
            del self.tasks[k]
            if len(self.tasks) < MAX_TASKS:
                break
