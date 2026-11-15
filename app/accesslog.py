"""access log 过滤：把前端轮询刷屏的那几行静音，但保留报错。

前端在有待处理任务时每 1.5 秒拉一次 /api/tasks（空闲时降到 10 秒），每一条都会
在 uvicorn 的 access log 里打一行，`docker logs` 很快只剩这一种重复行。
这里只滤掉「成功的轮询请求」；4xx/5xx 照常打印——排查时最需要的正是它们。
"""

import logging
from typing import Iterable

QUIET_PATHS = ("/api/tasks", "/api/files")


class QuietPollingFilter(logging.Filter):
    """丢掉 GET + 200 的轮询请求，其余一律放行。"""

    def __init__(self, paths: Iterable[str] = QUIET_PATHS):
        super().__init__()
        self.paths = tuple(paths)

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn 的 access 记录是 logger.info('%s - "%s %s HTTP/%s" %d', ...)，
        # args = (client_addr, method, path, http_version, status_code)
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        method, path, status = args[1], str(args[2]), args[4]
        if method != "GET" or not isinstance(status, int) or status >= 400:
            return True
        return path.split("?", 1)[0] not in self.paths


def install(disabled: bool = False, paths: Iterable[str] = QUIET_PATHS) -> None:
    """挂到 uvicorn.access 上；重复调用不会叠加多个过滤器。"""
    if disabled:
        return
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, QuietPollingFilter) for f in logger.filters):
        logger.addFilter(QuietPollingFilter(paths))
