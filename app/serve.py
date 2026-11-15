"""容器入口：先按环境变量配置日志，再启动 uvicorn。

相比 `uvicorn app.main:app` 直接启动，这里能保证 uvicorn 自己的启动日志
（Started server process / Application startup complete）也进日志文件。
"""

import os
import logging

import uvicorn

from .logconfig import setup_logging

log = logging.getLogger(__name__)


def _port() -> int:
    """读取 PORT；非法值回退 8080，避免容器在 restart 策略下无限重启。"""
    raw = os.environ.get("PORT", "8080")
    try:
        port = int(raw)
    except (TypeError, ValueError):
        log.warning("PORT=%r 不是合法端口，回退到 8080", raw)
        return 8080
    if not 1 <= port <= 65535:
        log.warning("PORT=%r 超出 1-65535，回退到 8080", raw)
        return 8080
    return port


def main() -> None:
    setup_logging()
    # 延迟导入：让日志先配置好，再加载应用（导入期的日志也能落盘）
    from .main import app

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=_port(),
        log_config=None,  # 用上面配好的 root handler，别让 uvicorn 覆盖
    )


if __name__ == "__main__":
    main()
