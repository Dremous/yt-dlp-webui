"""收集并脱敏 yt-dlp 的运行日志。

为什么单独成一个模块：
- yt-dlp 只装在容器里，本地开发机没有装；把日志逻辑放在这里（只依赖标准库），
  本地就能对它做单元测试，不必为了验证脱敏逻辑而启动整个下载流程。
- 职责单一：收集 → 脱敏 → 生成单行摘要 / 落盘。

yt-dlp 的 `logger` 选项接受任何带 debug/info/warning/error 四个方法的对象，
因此 TaskLogger 可以直接作为 logger 传入。
"""

import re
import threading
from collections import deque
from pathlib import Path
from typing import Deque, List, Optional

# 匹配 URL 中的 userinfo（user:password@）。
# 用 `*` 而不是 `+`：贪婪匹配到最后一个 @，这样密码里直接写了 @ 时也能整体抹掉。
# 例如 socks5://user:p@ss@host 若只匹配到第一个 @，会漏出 "ss" 这段密码。
# 字符类排除 / 空白 ? #，因此不会跨越路径或查询串，
# 避免把 `...?email=a@b.com` 这类普通 URL 误伤成 `...?email=***@b.com`。
_USERINFO_RE = re.compile(r"(://)[^/\s?#]*@")


def _redact(text: str) -> str:
    """把 URL 里的凭证替换成 ***，保留协议与主机，方便排查代理是否配置正确。"""
    return _USERINFO_RE.sub(r"\1***@", text)


class TaskLogger:
    """线程安全的日志缓冲，可直接传给 yt-dlp 的 `logger` 选项。

    max_lines：内存中保留的最大行数，超出后丢弃最早的行（环形缓冲）
    max_line_len：单行最大字符数，超长的媒体 URL 会被截断，避免撑爆内存
    """

    def __init__(self, max_lines: int = 2000, max_line_len: int = 2000):
        self._lines: Deque[str] = deque(maxlen=max_lines)
        # error 级别消息单独留存一份，用于生成给前端看的单行摘要
        self._errors: Deque[str] = deque(maxlen=5)
        self._max_line_len = max_line_len
        self._lock = threading.Lock()  # yt-dlp 可能从多个分片线程写日志

    # ------------------------------------------------------------------
    # yt-dlp logger 协议
    # ------------------------------------------------------------------
    def debug(self, msg: str) -> None:
        self._append(msg, is_error=False)

    def info(self, msg: str) -> None:
        self._append(msg, is_error=False)

    def warning(self, msg: str) -> None:
        self._append(msg, is_error=False)

    def error(self, msg: str) -> None:
        self._append(msg, is_error=True)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _append(self, msg: str, is_error: bool) -> None:
        # yt-dlp 会把 ERROR 行和整段 traceback 作为同一条消息传进来（消息内含多个 \n）。
        # 必须按物理行拆开：
        # - 缓冲里每个元素才真的是「一行」，LOG_TAIL_LINES 才名副其实
        # - error 摘要只取首行（即 "ERROR: ..."），不让 traceback 路径挤占前端那一行
        # 顺序仍然重要：每行都是先脱敏再截断，截断点落在密码中间也不留残片。
        lines: List[str] = []
        for raw in msg.splitlines():
            if not raw.strip():
                continue  # 消息内部的空行不占缓冲位
            line = _redact(raw.rstrip())
            if len(line) > self._max_line_len:
                line = line[: self._max_line_len]
            lines.append(line)

        if not lines:
            return

        with self._lock:
            self._lines.extend(lines)
            if is_error:
                self._errors.append(lines[0])  # 首行才是机器可读的错误结论

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------
    def redacted_lines(self) -> List[str]:
        """返回已脱敏日志行的副本（用于打印到容器日志）。"""
        with self._lock:
            return list(self._lines)

    def error_summary(self) -> Optional[str]:
        """生成给前端展示的单行摘要。

        前端把 task.error 放在单个 <p> 里渲染，\\n 会被折叠成空格，
        所以这里只返回一行：优先最后一条 ERROR，其次最后一条日志。
        """
        with self._lock:
            if self._errors:
                return self._errors[-1]
            if self._lines:
                return self._lines[-1]
        return None

    def write(self, path: Path) -> None:
        """把当前缓冲写入文件；父目录不存在时自动创建。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = self.redacted_lines()
        text = "\n".join(lines) + ("\n" if lines else "")
        path.write_text(text, encoding="utf-8")
