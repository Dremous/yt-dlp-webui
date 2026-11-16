"""下载管理器：基于 yt-dlp 的 Python API 实现后台下载任务。

设计要点：
- 所有 URL 都通过 yt-dlp 的 Python API 传递（绝不使用 shell 拼接），避免命令注入。
- 每个任务写入自己的子目录，产物按任务隔离，消除并发误判。
- 用信号量限制最大并发，用上限清理完成任务，防止线程/内存无限增长。
- Cookie 与代理都以「文件持久化 + 内存缓存」的方式提供，改完立即对新任务生效。
"""

import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import yt_dlp

from .store import TaskStore
from .tasklog import TaskLogger, redact

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 输出格式预设
# ---------------------------------------------------------------------------
# 每个预设就是一段 yt-dlp opts，前端下拉框完全由 /api/formats 驱动——加一档只改这里，
# 前端不用动。
#
# 视频预设的通用写法：
# - `bv` + `ba` 分别取最好的纯视频流与纯音频流再合并。YouTube 的高分辨率是音视频分离的，
#   只写 bv 会得到无声视频；`/b` 兜住「只有合并流」的站点（哔哩哔哩、部分自建站）。
# - 先试 mp4 + m4a 的组合，再退化到任意编码，最后退化到单一流：某档分辨率只有 AV1/VP9
#   时，前一条匹配不到也不会整单失败。
# - merge_output_format=mp4 保证成品是 .mp4，而不是 ffmpeg 默认挑的 .mkv/.webm。
# 音频预设统一 bestaudio + FFmpegExtractAudio，码率由 preferredquality 决定；
# m4a 那档不转码，直接把原始音频流抽出来。
def _mp3_options(quality: str) -> dict:
    """MP3 预设：只下音轨，再按指定码率转码。"""
    return {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality,
            }
        ],
    }


def _video_options(max_height: Optional[int] = None,
                   prefer_avc: bool = False) -> dict:
    """MP4 视频预设：按分辨率上限选流，并尽量凑成 mp4 + m4a。"""
    if prefer_avc:
        # avc1 = H.264：老设备、剪辑软件、部分播放器只认它。YouTube 的 H.264 最高到 1080p
        chain = (
            "bv[vcodec^=avc1]+ba[ext=m4a]"
            "/bv[ext=mp4]+ba[ext=m4a]"
            "/b[ext=mp4]/b"
        )
    else:
        cap = f"[height<={max_height}]" if max_height else ""
        chain = (
            f"bv{cap}[ext=mp4]+ba[ext=m4a]"
            f"/bv{cap}+ba"
            f"/b{cap}"
        )
    return {"format": chain, "merge_output_format": "mp4"}


# 默认档位：界面预选、API 未传 format 时都用它。改默认只改这一处（前端另有硬编码
# 兜底值，见 web/src/components/DownloadForm.jsx）。
DEFAULT_FORMAT = "best"

# yt-dlp 的 postprocessor hook 会带 `postprocessor` 名称（pp_key()，如
# Merger / ExtractAudio），用它把泛化的“合并 / 转码中”细分成用户能看懂的具体
# 阶段。未知后处理器一律回退到“后期处理中”，避免把元数据整理等步骤误报成转码。
POSTPROCESSOR_STAGE_LABELS = {
    "Merger": "合并中",
    "ExtractAudio": "转码中",
    "VideoConvertor": "转码中",
    "VideoRemuxer": "转码中",
    "SubtitlesConvertor": "转码中",
    "ThumbnailsConvertor": "转码中",
}
POSTPROCESSOR_STAGE_FALLBACK = "后期处理中"

FORMATS: Dict[str, dict] = {
    # ---- 视频 ----
    # 默认档：不转码，直接拿站点给的最高画质（可能是 webm / AV1）
    "best": {
        "label": "最佳格式（默认，保留原始编码）",
        "options": {
            "format": "bestvideo+bestaudio/best",
        },
    },
    "mp4": {
        "label": "MP4 视频（最高 mp4 画质）",
        "options": {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
        },
    },
    "mp4_2160": {"label": "MP4 4K（2160p）", "options": _video_options(2160)},
    "mp4_1440": {"label": "MP4 2K（1440p）", "options": _video_options(1440)},
    "mp4_1080": {"label": "MP4 1080p", "options": _video_options(1080)},
    "mp4_720": {"label": "MP4 720p", "options": _video_options(720)},
    "mp4_480": {"label": "MP4 480p（省流量）", "options": _video_options(480)},
    "mp4_avc": {
        "label": "MP4 H.264（老设备 / 剪辑软件兼容）",
        "options": _video_options(prefer_avc=True),
    },
    # ---- 音频 ----
    "mp3": {"label": "MP3 音频（192kbps）", "options": _mp3_options("192")},
    "mp3_320": {"label": "MP3 音频（320kbps 高品质）", "options": _mp3_options("320")},
    "mp3_128": {"label": "MP3 音频（128kbps 省空间）", "options": _mp3_options("128")},
    "m4a": {
        "label": "原始音频（不转码，m4a / opus）",
        "options": {"format": "bestaudio[ext=m4a]/bestaudio/best"},
    },
}

# 资源上限（防止无限增长 / 无节制并发）
MAX_CONCURRENT = 5     # 最大并发下载数
MAX_TASKS = 50         # 内存中保留的最大任务记录数（正常手工下载几乎不会触顶，仅防脚本刷任务）


def _parse_size(raw: str) -> int:
    """解析 `512M` / `1024K` / 纯字节的配置值；0、空串、负数、写错一律当「不分块」。"""
    raw = (raw or "").strip().lower()
    if raw in ("", "0", "off", "none"):
        return 0
    unit = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}.get(raw[-1:], 1)
    num = raw[:-1] if raw[-1:] in ("k", "m", "g") else raw
    try:
        value = int(float(num) * unit)
    except (ValueError, OverflowError):
        # 写错或数值超出范围（inf）都退回单连接：这是模块级常量，
        # 抛在 import 阶段等于容器起不来
        return 0
    return value if value > 0 else 0


# 单块大小（字节），0 = 不分块、一条连接直下。
# yt-dlp 的 http 下载器每块结束时会主动换连接（NextFragment，且不消耗重试次数），
# 所以块越小连接越密、越容易被服务端按频次限流；块越大则单次失败重下的越多。
# 默认 512M：20GB 的文件约 38 次请求，单次断流最多损失 512MB。
HTTP_CHUNK_SIZE = _parse_size(os.environ.get("HTTP_CHUNK_SIZE", "512M"))

# 下载中可能残留的临时/中间文件后缀，不视作最终产物（列表扫描时同样过滤）
# 注意：.log 是失败时留下的排查日志，必须排除，否则会被当成下载好的成品返回给前端
TMP_SUFFIXES = (
    ".part", ".ytdl", ".tmp", ".temp", ".webp",
    ".jpg", ".png", ".description", ".info.json", ".log",
)

# HLS/DASH 分片下载时，yt-dlp 会把分片临时文件写成 `.part-FragN`；
# 它不以 `.part` 结尾，只靠 endswith(TMP_SUFFIXES) 会漏掉。
_PART_FRAG_RE = re.compile(r"\.(?:part|ytdl)-Frag\d+$")


def is_temp_name(name: str) -> bool:
    """判断文件名是否是下载中的临时/中间产物（含 `.part-FragN`）。"""
    return name.endswith(TMP_SUFFIXES) or bool(_PART_FRAG_RE.search(name))


# 失败时保留在任务子目录里的 yt-dlp 详细日志文件名
LOG_NAME = "yt-dlp.log"
# 失败时打印到容器日志（docker logs）的末段行数
LOG_TAIL_LINES = 30

# 已结束的任务状态：只有这些状态允许从列表删除，进行中的必须先取消
ENDED_STATES = ("finished", "failed", "cancelled")

# 重启后给中断任务的说明：.part 还在，点重试即可续传
RESTART_ERROR = "服务重启导致任务中断，点「重试」可继续（已下载的部分会尽量续传）"

# 允许的代理协议：socks5h 表示由代理端解析域名，容器/远程代理场景更省事
ALLOWED_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")


def normalize_proxy(url: str) -> str:
    """校验并规范化代理地址；非法时抛出 ValueError（中文提示，直接反馈给前端）。

    允许省略协议，如只填 `host.docker.internal:7890`，缺省按 http:// 处理。

    `socks5://` 会被升级成 `socks5h://`：两者唯一的差别是域名由谁解析——
    socks5 由客户端在本地解析（PySocks 默认 rdns=False），容器里的 DNS 往往拿到
    被污染或离代理很远的 googlevideo 记录，连上去容易被对端直接 RST；
    socks5h 把主机名交给代理端解析，与代理出口的地理位置一致，才是容器场景该用的。
    输入仍接受 socks5（老配置不必手改），落盘与运行时一律用 socks5h。
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
    # 只改协议头，保留 userinfo / 主机 / 端口原样
    if parsed.scheme == "socks5":
        raw = "socks5h://" + raw.split("://", 1)[1]
    return raw


def mask_proxy_url(url: str) -> tuple[str, bool]:
    """把代理 URL 中的 userinfo 换成 ***，返回 (展示地址, 是否含凭证)。

    代理状态接口必须使用这个函数：页面只需要知道“配了哪个主机/端口”，
    不需要把 user:password 再回传给浏览器。真实地址仍只保存在 0600 的配置文件里。
    """
    parsed = urllib.parse.urlparse(url)
    has_credentials = bool(parsed.username or parsed.password)
    if not has_credentials:
        return url, False
    host = parsed.hostname or ""
    if ":" in host:  # IPv6 放进 netloc 时需要方括号
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = f"***@{host}"
    if port is not None:
        netloc += f":{port}"
    return parsed._replace(netloc=netloc).geturl(), True


class DownloadCancelled(Exception):
    """下载被用户主动取消（或排队超限被清理）时抛出的内部信号。"""

    pass


class DownloadTask:
    """单个下载任务的状态。"""

    def __init__(self, task_id: str, url: str, fmt: str):
        self.id = task_id
        self.url = url
        self.format = fmt
        self.status = "queued"          # queued / downloading / finished / failed / cancelled
        self.progress: float = 0.0      # 0 ~ 100
        self.speed: Optional[str] = None
        self.eta: Optional[str] = None
        self.stage: Optional[str] = None  # 下载结束后的后期处理阶段（合并中 / 转码中 / 后期处理中）
        self.title: Optional[str] = None
        self.filename: Optional[str] = None  # 相对下载目录的路径（含任务子目录前缀）
        self.error: Optional[str] = None
        self.created: float = time.time()
        # 多流（视频轨 + 音频轨）是分两次下载的：已下完的流字节数累计在这里，
        # 作为跨流进度的分子；总大小只从 requested_formats 求一次，免得每帧重算
        self._stream_done: float = 0.0
        self._grand_total: Optional[float] = None
        # 上次落盘时的进度，用来做 1% 节流，避免每帧都写 SQLite
        self._persisted_progress: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "format": self.format,
            "status": self.status,
            "progress": round(self.progress, 1),
            "stage": self.stage,
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
    store：可选。任务记录的持久化后端（见 app/store.py）。传入时启动即恢复历史任务。
    """

    def __init__(self, download_dir: Path, cookie_file: Optional[str] = None,
                 proxy_file: Optional[str] = None, store: Optional[TaskStore] = None):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.cookie_file = Path(cookie_file) if cookie_file else None
        self.proxy_file = Path(proxy_file) if proxy_file else None
        self.tasks: Dict[str, DownloadTask] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT)
        self._proxy: Optional[str] = None  # 当前生效的代理地址（None = 直连）
        self._store = store
        # 正在删除的任务：remove_task 删库成功到删内存之间，persist 不能再 INSERT
        self._deleting: set = set()
        self._load_proxy()
        if self._store is not None:
            self._restore_tasks()

    # ------------------------------------------------------------------
    # Cookie 支持
    # ------------------------------------------------------------------
    def cookie_ready(self) -> bool:
        """是否存在可用的 Cookie 文件。"""
        return self.cookie_file is not None and self.cookie_file.is_file()

    def save_cookie(self, data: bytes) -> None:
        """保存 Cookie 文件内容（供后端上传接口调用），并收紧文件权限。

        采用「临时文件 + 原子替换」写入，避免并发下载读取到写了一半的 cookie；
        临时文件名由 mkstemp 唯一生成，并发上传不会互相覆盖同一个 .tmp。
        """
        if self.cookie_file is None:
            raise ValueError("未配置 COOKIE_FILE，无法保存 Cookie")
        self._atomic_write(self.cookie_file, data, binary=True)

    def _atomic_write(self, path: Path, data, *, binary: bool) -> None:
        """写入同目录临时文件后原子替换；失败时清理临时文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        tmp = Path(tmp_name)
        try:
            if binary:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

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
        """返回代理状态，供前端展示。

        只返回主机/端口等展示信息；userinfo 用 *** 替换，避免接口把代理密码
        原样回传给浏览器。需要修改凭证时，前端应要求重新输入完整地址。
        """
        proxy = self.proxy
        if not proxy:
            return {"active": False, "url": None, "has_credentials": False}
        display_url, has_credentials = mask_proxy_url(proxy)
        return {
            "active": True,
            "url": display_url,
            "has_credentials": has_credentials,
        }

    def save_proxy(self, url: str) -> str:
        """保存代理地址并立即生效，返回规范化后的地址。

        与 save_cookie 同策略：先校验再「临时文件 + 原子替换」写入，
        避免并发下载读到写了一半的内容；代理常含账号密码，权限收为 0600。
        """
        if self.proxy_file is None:
            raise ValueError("未配置 PROXY_FILE，无法保存代理")
        proxy = normalize_proxy(url)  # 非法地址在落盘前就拒绝
        # 写文件与更新内存必须在同一临界区：否则两个并发保存可能一个写盘
        # 成功、另一个更新内存成功，重启后代理会从当前值“变”成另一个值。
        with self._lock:
            self._atomic_write(self.proxy_file, proxy + "\n", binary=False)
            self._proxy = proxy  # 立即对后续任务生效，无需重启容器
        return proxy

    def remove_proxy(self) -> None:
        """删除已保存的代理配置并恢复直连。"""
        with self._lock:
            if self.proxy_file is not None and self.proxy_file.exists():
                self.proxy_file.unlink()
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
        self.persist(task)
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

    def count_ended(self) -> int:
        """已结束的任务数（全量）。

        snapshot() 会被 limit 截断，前端拿它统计「有没有可清理的任务」时
        会漏掉列表之外的那些，所以单独提供一个不受 limit 影响的计数。
        """
        with self._lock:
            return sum(1 for t in self.tasks.values() if t.status in ENDED_STATES)

    def cancel(self, task_id: str) -> DownloadTask:
        """在锁内把任务迁移到 cancelled，避免和下载线程的终态写入互相覆盖。

        KeyError：任务不存在；ValueError：任务已经结束，不能再取消。
        """
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status in ENDED_STATES:
                raise ValueError("任务已结束，无法取消")
            task.status = "cancelled"
            task.stage = None
            task.speed = None
            task.eta = None
        self.persist(task)
        return task

    # ------------------------------------------------------------------
    # 任务持久化
    # ------------------------------------------------------------------
    def persist(self, task: DownloadTask) -> None:
        """把任务当前状态写盘；任务已被删除或没配置 store 时什么也不做。

        删除与落盘通过 _deleting 墓碑串行：remove_task 先在锁内登记墓碑，再
        删库、删内存；后台线程随后带着终态调用 persist 时，墓碑会让它直接返回。
        只检查 self.tasks 里“对象是否还在”不够，因为删库成功到删内存之间
        存在一个窗口，晚到的 INSERT 会让任务在重启后“复活”。

        SQLite 写入不放在 _lock 内：先在锁内复制一份字段快照，释放锁后再写；
        写完再检查一次删除状态，如果期间任务被删，立即补一次 DELETE。
        这样慢盘/锁库时不会阻塞 snapshot()、cancel() 等状态读取。
        """
        if self._store is None:
            return
        with self._lock:
            if task.id in self._deleting or self.tasks.get(task.id) is not task:
                return
            values = self._store.task_values(task)
        try:
            self._store.save_values(values)
        finally:
            with self._lock:
                if task.id in self._deleting or self.tasks.get(task.id) is not task:
                    # 快照到落库之间任务被删：把晚到的 INSERT 撤掉，避免重启复活。
                    self._store.delete(task.id)

    def _restore_tasks(self) -> None:
        """启动时恢复任务记录。

        未完成（queued / downloading）的任务在进程重启后不可能继续跑，统一标成
        failed 并写明原因；它们的 .part 留在原目录，用户点「重试」时
        _adopt_partial 会把这个任务当捐赠者，把残片接管到新任务目录接着下。
        """
        rows = self._store.load()
        if len(rows) > MAX_TASKS:
            # 与内存上限保持一致：尾部留最新 MAX_TASKS 条，其余连同记录一起清掉。
            # 删库失败时不能只截断内存 rows：必须把没删掉的旧行继续留在内存里，
            # 否则界面看不到、DB 里还在，下次启动又会冒出来。
            old_rows = rows[:-MAX_TASKS]
            deleted_ids = set()
            trim_failed = False
            for row in old_rows:
                if not self._store.delete(row["id"]):
                    trim_failed = True
                    break
                deleted_ids.add(row["id"])
            if trim_failed:
                rows = [row for row in rows if row["id"] not in deleted_ids]
                log.warning(
                    "任务库启动裁剪未完成：%d 条旧记录仍保留在库中",
                    len(rows) - MAX_TASKS,
                )
            else:
                rows = rows[-MAX_TASKS:]

        restored = []
        for row in rows:
            task = DownloadTask(row["id"], row["url"], row["format"])
            task.status = row["status"]
            task.progress = float(row["progress"] or 0.0)
            task.stage = row["stage"]
            task.title = row["title"]
            task.filename = row["filename"]
            task.error = row["error"]
            task.created = float(row["created"] or time.time())
            task._persisted_progress = task.progress
            if task.status in ("queued", "downloading"):
                task.status = "failed"
                task.stage = None
                task.speed = None
                task.eta = None
                # 与运行时失败保持一致：失败任务的进度归零，界面不会出现
                # 「失败 + 87%」这种自相矛盾的状态。已下载的残片仍在任务目录，
                # 点「重试」时 _adopt_partial 会按 .part 大小接管，不依赖这个数值。
                task.progress = 0.0
                task._persisted_progress = 0.0
                task.error = RESTART_ERROR
                self._store.save(task)
            restored.append(task)

        with self._lock:
            for task in restored:
                self.tasks[task.id] = task

    # ------------------------------------------------------------------
    # 任务删除（只处理已结束的任务，避免与后台线程争抢同一目录）
    # ------------------------------------------------------------------
    def _delete_record(self, task_id: str) -> bool:
        """删除 SQLite 行；store 未配置时视为无需删库。

        降级模式下 TaskStore.delete 会写删除墓碑，因此这里不能因为
        store.available 为 False 就直接返回 True：那会只删内存、留下 DB 行，
        重启后任务又复活。墓碑也写不进去时才返回 False。
        """
        if self._store is None:
            return True
        return self._store.delete(task_id)

    def remove_task(self, task_id: str) -> dict:
        """删除一个已结束的任务记录，返回 {"id", "removed_files"}。

        磁盘策略（与界面上的确认文案一致）：
        - failed / cancelled：目录里只有 .part、.log 等残留，连目录一起删
        - finished：视频文件还有用，只删记录、保留目录

        任务不存在抛 KeyError；仍在排队/下载中抛 ValueError，调用方应提示先取消。
        """
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status not in ENDED_STATES:
                raise ValueError("任务尚未结束，请先取消")
            self._deleting.add(task_id)

        try:
            # 先删库/写墓碑、再删内存：两者都失败时任务仍留在列表里，不会出现
            # 「界面已删、重启后又复活」的状态；TaskStore.delete 失败返回 False。
            if self._store is not None and not self._store.delete(task_id):
                raise ValueError("任务库当前不可用，任务未删除，请稍后重试")

            with self._lock:
                if self.tasks.get(task_id) is task:
                    del self.tasks[task_id]
        finally:
            with self._lock:
                self._deleting.discard(task_id)

        # 目录清理放在锁外：rmtree 可能较慢，不该阻塞其他任务的状态读取。
        # 这里只是「尽力删除」：取消后后台线程要等下一次 progress hook 才退出，
        # 两边可能同时动这个目录。rmtree 都带 ignore_errors 所以不会抛。
        # 与失败日志落盘的竞态由 _report_failure 兜底：它在落盘前后各查一次
        # 任务是否还在，必要时把刚写回的目录再删掉。
        discard = task.status in ("failed", "cancelled")
        if discard:
            shutil.rmtree(self.download_dir / task_id, ignore_errors=True)
        return {"id": task_id, "removed_files": discard}

    def remove_finished(self) -> int:
        """清理所有已结束的任务记录，返回删除数量；进行中的任务不受影响。"""
        with self._lock:
            ended = [
                (tid, t.status)
                for tid, t in self.tasks.items()
                if t.status in ENDED_STATES
            ]
            self._deleting.update(tid for tid, _ in ended)

        removed = []
        try:
            if self._store is None:
                removed = ended
            else:
                for tid, status in ended:
                    if not self._store.delete(tid):
                        break  # 库和墓碑都不可用：已经删掉的部分保留，其余留在列表里
                    removed.append((tid, status))

            with self._lock:
                for tid, _ in removed:
                    self.tasks.pop(tid, None)
        finally:
            with self._lock:
                self._deleting.difference_update(tid for tid, _ in ended)

        for tid, status in removed:
            if status in ("failed", "cancelled"):
                shutil.rmtree(self.download_dir / tid, ignore_errors=True)
        return len(removed)

    # ------------------------------------------------------------------
    # 内部执行
    # ------------------------------------------------------------------
    def _run(self, task: DownloadTask) -> None:
        # 用信号量限制最大并发；排队任务在此等待空闲并发位
        self._slots.acquire()
        try:
            # 排队期间被取消/清理：acquire 到后直接返回，避免孤儿下载
            with self._lock:
                if self.tasks.get(task.id) is not task or task.status != "queued":
                    return
            self._download(task)
        finally:
            self._slots.release()

    def _begin_download(self, task: DownloadTask) -> bool:
        """在锁内把任务从 queued 迁移到 downloading；已取消/已删除则返回 False。"""
        with self._lock:
            if self.tasks.get(task.id) is not task or task.status != "queued":
                return False
            task.status = "downloading"
            task.stage = None
            task.speed = None
            task.eta = None
        self.persist(task)
        return True

    def _finish(self, task: DownloadTask, title: Optional[str],
                filename: Optional[str]) -> bool:
        """在锁内检查取消后再标记 finished，避免完成状态覆盖取消。"""
        with self._lock:
            if self.tasks.get(task.id) is not task or task.status == "cancelled":
                return False
            task.title = title
            task.filename = filename
            task.status = "finished"
            task.progress = 100.0
            task.stage = None
            task.speed = None
            task.eta = None
        self.persist(task)
        return True

    def _fail(self, task: DownloadTask, error: str) -> bool:
        """在锁内检查取消后再标记失败；任务已取消时返回 False。"""
        with self._lock:
            if self.tasks.get(task.id) is not task or task.status == "cancelled":
                return False
            task.status = "failed"
            task.stage = None
            task.speed = None
            task.eta = None
            task.progress = 0.0
            task.error = error
        self.persist(task)
        return True

    def _cleanup_cancelled(self, task: DownloadTask, task_dir: Path) -> None:
        """把已取消任务落盘并清理目录；任务已被删除时不要重新建记录。"""
        with self._lock:
            if self.tasks.get(task.id) is not task:
                return
            task.status = "cancelled"
            task.stage = None
            task.speed = None
            task.eta = None
            task.progress = 0.0
        self.persist(task)
        shutil.rmtree(task_dir, ignore_errors=True)

    def _is_cancelled(self, task: DownloadTask) -> bool:
        with self._lock:
            return task.status == "cancelled"

    def _download(self, task: DownloadTask) -> None:
        task_dir = self.download_dir / task.id
        # 先占住 queued -> downloading；如果排队期间已经取消，连目录都不建
        try:
            started = self._begin_download(task)
        except Exception as exc:  # noqa: BLE001 - 持久化异常不能把任务线程直接打死
            self._fail(task, f"任务初始化失败：{exc}")
            return
        if not started:
            return
        task_logger = TaskLogger()
        try:
            # 每个任务写入自己的子目录，产物互不干扰
            task_dir.mkdir(parents=True, exist_ok=True)
            if self._is_cancelled(task):
                self._cleanup_cancelled(task, task_dir)
                return
            # 重新提交同一链接时，把上次失败留下的临时文件接管过来，避免从头再下一遍
            adopted = self._adopt_partial(task, task_dir)
            if self._is_cancelled(task):
                self._cleanup_cancelled(task, task_dir)
                return
            if adopted:
                task_logger.info(f"[webui] 接管上次未完成的分片：{adopted}，从断点续传")
            opts = {
                "outtmpl": str(task_dir / "%(title).100B [%(id)s].%(ext)s"),
                "noplaylist": True,          # 只下载单个视频，不展开播放列表
                "noprogress": True,
                # 网络容错：Python API 里 retries 缺省为 0（CLI 的默认 10 是命令行层给的），
                # 等于「断一次就整体放弃」。http 下载器重试时会从 .part 的字节数发 Range 续传，
                # 所以大文件经代理下载被中途掐断时，有重试才不会白下一遍。
                "retries": 20,               # 单文件（含续传）重试次数
                "fragment_retries": 20,      # HLS/DASH 分片重试次数
                "socket_timeout": 30,        # 单次 socket 读超时（秒），默认 20
                # 重试间隔：又是「CLI 有、API 没有」的默认值——RetryManager 只在
                # retry_sleep_functions 里拿到可调用对象时才 sleep，缺省是 None，
                # 于是 20 次重试会在几百毫秒内连着重连一遍。对服务端来说这是刷流量，
                # 很容易从「连接被掐」升级成 403；在 Clash 的连接面板里就是
                # 「连上—断开—立刻再连」刷屏。这里给指数退避（1,2,4,…,30 秒封顶）。
                "retry_sleep_functions": {
                    "http": lambda n: min(2 ** n, 30),
                    "fragment": lambda n: min(2 ** n, 30),
                },
                # 大文件分块下载：每块结束主动换一条连接，单次断流最多重下这一块。
                # 512M 对 20GB 约 38 次请求，既不会把连接拆得过碎，也不会一次丢掉几十 GB。
                "http_chunk_size": HTTP_CHUNK_SIZE,
                # verbose + 自定义 logger：把 debug 级输出交给 TaskLogger。
                # 这里刻意不设 quiet / no_warnings —— 它们会把错误原因静音，
                # 像偶发 403 这类问题就完全无从排查。
                "verbose": True,
                "logger": task_logger,
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

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(task.url, download=True)
            title = (info or {}).get("title")
            filename = self._task_output(task_dir)
            if not filename:
                # yt-dlp 返回成功但目录里没有成品（例如只写了被过滤的中间文件，
                # 或目录被外部清空）不能显示成“已完成但没有下载链接”。
                if not self._fail(task, "下载结束但未找到输出文件"):
                    self._cleanup_cancelled(task, task_dir)
                return
            if not self._finish(task, title, filename):
                self._cleanup_cancelled(task, task_dir)
        except DownloadCancelled:
            # 用户主动取消：清空进度，标记已取消，并清理任务目录（含 .part 残留）
            self._cleanup_cancelled(task, task_dir)
        except Exception as exc:  # noqa: BLE001 - 需要把任意下载异常反馈给前端
            # 取消与异常可能同时到达：取消优先，不能把已取消任务改回 failed
            if self._is_cancelled(task):
                self._cleanup_cancelled(task, task_dir)
                return
            error = self._report_failure(task, task_dir, task_logger, exc)
            if not self._fail(task, error):
                self._cleanup_cancelled(task, task_dir)
            # 失败：仅清理空目录（保留可能的中间文件与日志供排查/重试）
            try:
                if not any(task_dir.iterdir()):
                    task_dir.rmdir()
            except OSError:
                pass

    def _adopt_partial(self, task: DownloadTask, task_dir: Path) -> Optional[str]:
        """把同一链接的历史任务留下的临时文件搬进本次任务目录，实现跨任务续传。

        为什么需要它：任务目录按 task_id 隔离（删除策略与并发隔离都依赖这一点），
        于是「失败后重新提交」天然落在新目录里——上次下了 90% 的 .part 留在旧目录，
        yt-dlp 在新目录里从 0 开始，20GB 的视频等于白下一遍。

        做法是开跑前用 os.replace 把残片改名搬过来：
        - rename 在同一文件系统内是原子的，同一个 .part 只会被一个任务拿到，
          并发提交同一链接不会出现两个线程写同一个文件；
        - 目标目录已有同名文件时跳过，不覆盖；
        - 只从一个捐赠者搬一整套残片，避免把不同下载尝试的 .part/.ytdl 拼在一起；
        - 搬走后旧目录只剩日志，删除历史任务不会误删正在续传的数据。

        只从已结束（failed / cancelled / finished）且**格式相同**的任务里取：不同分辨率的
        预设共用同一个 outtmpl，残片同名但字节内容不同，混着接管会拼出损坏的视频。
        正在下载的任务是活动写者，它的 .part 不能动。按捐赠者的总残片大小选一份，
        不跨捐赠者混拼。返回给日志用的一句话摘要，没接管到任何东西时返回 None。
        """
        candidates = []
        with self._lock:
            donors = [
                t.id for t in self.tasks.values()
                if t.id != task.id and t.url == task.url
                and t.format == task.format
                and t.status in ENDED_STATES
            ]
        for donor_id in donors:
            donor_dir = self.download_dir / donor_id
            if not donor_dir.is_dir():
                continue
            try:
                parts = list(donor_dir.iterdir())
            except OSError:
                # 用户可能正好删除了捐赠者目录；这不是新任务失败的理由，
                # 当作没接管到，本次从头下载即可。
                continue
            donor_parts = []
            for part in parts:
                # HLS/DASH 的 `.part-FragN` 和 yt-dlp 的 `.ytdl` 也是续传所需，
                # 不能只 glob("*.part")；成品文件与日志/图片不在这里搬。
                if not part.is_file():
                    continue
                if not (
                    part.name.endswith((".part", ".ytdl"))
                    or _PART_FRAG_RE.search(part.name)
                ):
                    continue
                try:
                    size = part.stat().st_size
                except OSError:
                    continue
                if size > 0:
                    donor_parts.append((size, part))
            if donor_parts:
                candidates.append((sum(size for size, _ in donor_parts), donor_parts))

        if not candidates:
            return None
        # 选总残片最大的捐赠者，整套搬过来；不要按单个文件名跨 donor 取最大。
        _, donor_parts = max(candidates, key=lambda item: item[0])

        moved_count, moved_bytes, seen = 0, 0, set()
        for size, part in donor_parts:
            if part.name in seen:
                continue
            seen.add(part.name)
            dst = task_dir / part.name
            if dst.exists():
                continue
            try:
                os.replace(part, dst)
            except OSError:
                continue  # 跨设备/权限问题：当作没接管，本次从头下载
            moved_count += 1
            moved_bytes += size

        if not moved_count:
            return None
        return f"{moved_count} 个分片，共 {moved_bytes / 1024 / 1024:.1f} MiB"

    def _is_live(self, task_id: str) -> bool:
        """任务记录是否还在，用于判断失败处理途中是否被用户删除。"""
        with self._lock:
            return task_id in self.tasks

    def _report_failure(self, task: DownloadTask, task_dir: Path,
                        task_logger: TaskLogger, exc: Exception) -> str:
        """留下失败现场：日志落盘 + 打印到容器日志 + 返回给前端的单行摘要。

        间歇性故障（例如偶发 403）无法预先复现，只能在失败当时把证据留住：
        否则下次依然只看到一句 "HTTP Error 403"，排查只能靠手动绕开 WebUI 跑命令。

        与 remove_task 的并发（两次检查缺一不可）：
        - 落盘前查一次：任务已删就不写。write() 会 mkdir 父目录，会把刚被
          rmtree 的目录重新建出来。
        - 落盘后再查一次：这中间 remove_task 可能刚好删完记录、清完目录，
          此时必须把刚写的日志目录回收，否则留下没有任务记录的孤儿目录。
        """
        log_path = task_dir / LOG_NAME
        log_rel: Optional[str] = None
        lines = task_logger.redacted_lines()

        if lines and self._is_live(task.id):
            try:
                task_logger.write(log_path)
                log_rel = f"{task.id}/{LOG_NAME}"
            except OSError:
                pass  # 落盘失败（磁盘满/目录被删）不能影响任务状态，摘要仍会给

            if log_rel and not self._is_live(task.id):
                # 写盘期间任务被删：目录连同刚落盘的日志一起回收
                shutil.rmtree(task_dir, ignore_errors=True)
                log_rel = None

            if log_rel:
                # 同时打印到 stderr，这样 docker logs 就能直接看到失败原因
                tail = lines[-LOG_TAIL_LINES:]
                print(f"[download] 任务 {task.id} 失败，yt-dlp 日志末 {len(tail)} 行：",
                      file=sys.stderr)
                print("\n".join(tail), file=sys.stderr)

        # 前端把 error 渲染在单个 <p> 里，\n 会折叠成空格，所以只给一行摘要。
        # 统一走 redact：即使 yt-dlp 抛出的异常里带 user:password，也不能进前端/任务库。
        summary = redact(task_logger.error_summary() or str(exc))[:400]
        return f"{summary}｜完整日志：{log_rel}" if log_rel else summary

    def _hook(self, data: dict, task: DownloadTask) -> None:
        # 取消信号：一旦任务被标记为取消，立即终止 yt-dlp 下载。
        # 状态由 cancel() 在锁内写入，这里统一走 _is_cancelled()，避免跨线程裸读。
        if self._is_cancelled(task):
            raise DownloadCancelled()
        # progress / postprocessor 回调结构不同，用 .get 防御缺键
        status = data.get("status")
        if status == "downloading":
            done, total = self._progress_totals(data, task)
            if total:
                task.progress = min(done / total * 100.0, 100.0)
            task.speed = data.get("_speed_str") or data.get("speed_str")
            task.eta = data.get("_eta_str") or data.get("eta_str")
            # 每 1% 落一次盘：重启后至少能恢复到最近的整数进度，又不至于每帧写库
            if task.progress - task._persisted_progress >= 1.0:
                task._persisted_progress = task.progress
                self.persist(task)
        elif status == "finished":
            # 有两类 finished：某一路流下载完成（带 downloaded_bytes），
            # 以及后期处理结束（不带）。前者要把字节数记账，后者只是清阶段提示
            if data.get("downloaded_bytes") is not None:
                task._stream_done += float(data["downloaded_bytes"] or 0)
                task.stage = None
            else:
                task.stage = None
            task.speed = None
            task.eta = None
            self.persist(task)
        elif status == "started":
            # postprocessor 钩子带 postprocessor 名称，按类型给出更精确的阶段。
            # 它的意义是让前端知道「下载已结束、正在后期处理」，而不是卡在 100%。
            postprocessor = data.get("postprocessor")
            task.stage = POSTPROCESSOR_STAGE_LABELS.get(
                postprocessor, POSTPROCESSOR_STAGE_FALLBACK
            )
            task.speed = None
            task.eta = None
            self.persist(task)

    @staticmethod
    def _progress_totals(data: dict, task: DownloadTask) -> tuple:
        """返回 (已下载字节, 总字节)。

        `best` 这类档位会选中「视频轨 + 音频轨」，yt-dlp 在 process_info 里对每个流
        各调一次下载器，因此每次回调看到的 total_bytes 都只是当前流的大小。
        直接用它算百分比，进度会先冲到 100%、换流后又掉回 0 走第二遍。
        这里用 requested_formats 的总和作分母，把已完成的流累加进分子。
        """
        if task._grand_total is None:
            fmts = (data.get("info_dict") or {}).get("requested_formats") or []
            sizes = [f.get("filesize") or f.get("filesize_approx") or 0 for f in fmts]
            # 只有多流且每个流都报了大小时才聚合，否则退回逐流百分比
            task._grand_total = sum(sizes) if len(fmts) > 1 and all(sizes) else 0
        downloaded = float(data.get("downloaded_bytes") or 0)
        if task._grand_total:
            return task._stream_done + downloaded, task._grand_total
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        return downloaded, total

    # ------------------------------------------------------------------
    # 产物定位 & 任务清理
    # ------------------------------------------------------------------
    def _task_output(self, task_dir: Path) -> Optional[str]:
        """返回任务子目录中最新的成品文件（排除临时/中间文件），相对下载目录的路径。"""
        try:
            entries = list(task_dir.iterdir())
        except OSError:
            return None  # 目录被并发删除时按没有成品处理
        found = []
        for p in entries:
            try:
                if not p.is_file() or is_temp_name(p.name):
                    continue
                found.append((p, p.stat().st_mtime))
            except OSError:
                continue  # 扫描期间文件被并发删除，跳过而非让任务失败
        if not found:
            return None
        newest = max(found, key=lambda item: item[1])[0]
        return newest.relative_to(self.download_dir).as_posix()

    def _trim_tasks(self) -> None:
        """任务数超上限时清理：先清已完成/失败/已取消，仍超再取消并清最老的排队任务。

        注意：这里只删任务记录，**不回收磁盘**——自动裁剪是背压手段，不是清理动作。
        失败/取消任务残留的 .part 与日志要靠界面上的「删除」或「清理已结束」回收。
        """
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
            # 先删库；失败就保留这条内存记录，否则 DB 行会在重启后复活。
            if not self._delete_record(k):
                return
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
            if not self._delete_record(k):
                break
            self.tasks[k].status = "cancelled"
            del self.tasks[k]
            if len(self.tasks) < MAX_TASKS:
                break
