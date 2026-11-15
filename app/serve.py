"""容器入口：先按环境变量配置日志，再启动 uvicorn。

相比 `uvicorn app.main:app` 直接启动，这里能保证 uvicorn 自己的启动日志
（Started server process / Application startup complete）也进日志文件。
"""

import os

import uvicorn

from .logconfig import setup_logging


def main() -> None:
    setup_logging()
    # 延迟导入：让日志先配置好，再加载应用（导入期的日志也能落盘）
    from .main import app

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        log_config=None,  # 用上面配好的 root handler，别让 uvicorn 覆盖
    )


if __name__ == "__main__":
    main()
