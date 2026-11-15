"""按环境变量把运行日志写到文件（可选轮转），同时保留控制台输出。

容器里 `docker logs` 默认只保留最近的 json 日志、容器一删就没了；把日志落进
`/config` 卷，问题现场就能翻。所有行为都可用环境变量关掉或调整：

    LOG_FILE     日志文件路径；默认 /config/webui.log，置 none/off/空 则不落盘
    LOG_LEVEL    级别，默认 INFO
    LOG_MAX_MB   单文件上限（MB），默认 10；0 表示不轮转
    LOG_BACKUPS  保留的历史文件个数，默认 5
    LOG_CONSOLE  是否同时打印到 stdout，默认 1；置 0 则只写文件
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Mapping, Optional

DEFAULT_LOG_FILE = "/config/webui.log"
DEFAULT_MAX_MB = "10"
DEFAULT_BACKUPS = "5"

# 标记自己挂的 handler，重复 setup 时先摘掉旧的，避免日志写多份
_OWNED = "_ytdlp_webui_handler"

_LEVELS = {
    "CRITICAL": logging.CRITICAL, "ERROR": logging.ERROR, "WARNING": logging.WARNING,
    "INFO": logging.INFO, "DEBUG": logging.DEBUG,
}


def _flag(value: Optional[str], default: str = "1") -> bool:
    return (value if value is not None else default).strip().lower() not in (
        "0", "false", "off", "no", "none", ""
    )


def setup_logging(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """配置 root logger（uvicorn 的 logger 会继承它）。

    返回实际写入的文件路径；未落盘或路径不可用时返回 None。
    路径不可写不应让容器启动失败——退回控制台即可。
    """
    source = env if env is not None else os.environ
    level = _LEVELS.get((source.get("LOG_LEVEL") or "INFO").strip().upper(), logging.INFO)
    console = _flag(source.get("LOG_CONSOLE"), "1")

    raw = source.get("LOG_FILE")
    if raw is None:
        raw = DEFAULT_LOG_FILE
    raw = raw.strip()
    file_path = None if raw.lower() in ("", "none", "off", "0") else raw

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _OWNED, False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        setattr(stream, _OWNED, True)
        root.addHandler(stream)

    saved: Optional[str] = None
    if file_path:
        try:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            max_bytes = int(float(source.get("LOG_MAX_MB") or DEFAULT_MAX_MB) * 1024 * 1024)
            backups = int(source.get("LOG_BACKUPS") or DEFAULT_BACKUPS)
            handler = RotatingFileHandler(
                file_path, maxBytes=max(0, max_bytes), backupCount=max(0, backups),
                encoding="utf-8",
            )
            handler.setFormatter(fmt)
            setattr(handler, _OWNED, True)
            root.addHandler(handler)
            saved = file_path
        except (OSError, ValueError) as exc:
            # 用户可能同时关了控制台：此时若一个 handler 都没有，
            # 连这条 warning 都会被吞掉，等于全部日志静默消失
            if not root.handlers:
                fallback = logging.StreamHandler()
                fallback.setFormatter(fmt)
                setattr(fallback, _OWNED, True)
                root.addHandler(fallback)
            logging.getLogger(__name__).warning(
                "日志文件不可用（%s）：%s；日志仅输出到控制台", file_path, exc
            )

    # uvicorn 自带的 handler 若还在，会导致同一条日志写两遍；统一交给 root
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if getattr(handler, _OWNED, False):
                logger.removeHandler(handler)
        logger.propagate = True
        if logger.level == logging.NOTSET:
            logger.setLevel(level)
    return saved
