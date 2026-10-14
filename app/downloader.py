"""下载管理器：基于 yt-dlp 的 Python API 实现后台下载任务。

所有 URL 都通过 yt-dlp 的 Python API 传递（绝不使用 shell 拼接），
从根上避免命令注入风险。
"""

import threading
import time
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
        self.filename: Optional[str] = None
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
    """

    def __init__(self, download_dir: Path, cookie_file: Optional[str] = None):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_file = Path(cookie_file) if cookie_file else None
        self.tasks: Dict[str, DownloadTask] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Cookie 支持
    # ------------------------------------------------------------------
    def cookie_ready(self) -> bool:
        """是否存在可用的 Cookie 文件。"""
        return self.cookie_file is not None and self.cookie_file.is_file()

    def save_cookie(self, data: bytes) -> None:
        """保存 Cookie 文件内容（供后端上传接口调用）。"""
        if self.cookie_file is None:
            raise ValueError("未配置 COOKIE_FILE，无法保存 Cookie")
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self.cookie_file.write_bytes(data)

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
    # 对外接口
    # ------------------------------------------------------------------
    def submit(self, url: str, fmt: str) -> DownloadTask:
        task = DownloadTask(uuid.uuid4().hex[:8], url, fmt)
        with self._lock:
            self.tasks[task.id] = task
        threading.Thread(target=self._run, args=(task,), daemon=True).start()
        return task

    def snapshot(self) -> List[dict]:
        """返回所有任务的可序列化快照（按创建时间倒序）。"""
        with self._lock:
            items = [t.to_dict() for t in self.tasks.values()]
        items.sort(key=lambda d: d["created"], reverse=True)
        return items

    def get(self, task_id: str) -> Optional[DownloadTask]:
        with self._lock:
            return self.tasks.get(task_id)

    # ------------------------------------------------------------------
    # 内部执行
    # ------------------------------------------------------------------
    def _run(self, task: DownloadTask) -> None:
        # 记录任务开始前目录中已存在的文件，结束后 diff 出实际产物文件名
        before = self._dir_files()

        task.status = "downloading"
        opts = {
            "outtmpl": str(self.download_dir / "%(title).100B [%(id)s].%(ext)s"),
            "noplaylist": True,          # 只下载单个视频，不展开播放列表
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [lambda d: self._hook(d, task)],
            **FORMATS[task.format]["options"],
        }

        # 若已导入 Cookie，则附加 cookiefile（用于登录站点/绕过限制）
        if self.cookie_ready():
            opts["cookiefile"] = str(self.cookie_file)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(task.url, download=True)
            task.title = info.get("title")
            task.filename = self._new_files(before)
            task.status = "finished"
            task.progress = 100.0
            task.speed = None
            task.eta = None
        except Exception as exc:  # noqa: BLE001 - 需要把任意下载异常反馈给前端
            task.status = "failed"
            task.error = str(exc)[:500]

    def _hook(self, data: dict, task: DownloadTask) -> None:
        if data["status"] == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes", 0)
            if total:
                task.progress = min(downloaded / total * 100.0, 100.0)
            task.speed = data.get("_speed_str") or data.get("speed_str")
            task.eta = data.get("_eta_str") or data.get("eta_str")

    # ------------------------------------------------------------------
    # 文件 diff 辅助
    # ------------------------------------------------------------------
    def _dir_files(self) -> Dict[str, float]:
        """返回目录中 {文件名: 修改时间} 的映射。"""
        return {
            p.name: p.stat().st_mtime_ns
            for p in self.download_dir.iterdir()
            if p.is_file()
        }

    def _new_files(self, before: Dict[str, float]) -> Optional[str]:
        """找出任务执行后新增或变化过的文件，返回最新一个。"""
        after = self._dir_files()
        candidates = []
        for name in after:
            # 新增，或 mtime 发生变化
            if name not in before or after[name] != before[name]:
                candidates.append(name)
        if not candidates:
            return None
        # 返回修改时间最新的
        newest = max(candidates, key=lambda n: after[n])
        return newest
