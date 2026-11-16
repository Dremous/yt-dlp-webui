# 项目结构

[返回 README](../README.md)

```
.
├── README.md                     # 项目入口与文档导航
├── docs/                         # 详细文档（从 README 链接进入）
│   ├── quickstart.md             # 功能、快速开始、输出格式
│   ├── network.md                # 代理与 Cookie
│   ├── troubleshooting.md        # 下载失败排查
│   ├── operations.md             # 构建、日志、本地开发、配置
│   ├── api.md                    # HTTP API
│   └── project-structure.md      # 本文件：目录与测试说明
├── Dockerfile                    # 多阶段构建（node 前端 → deno → python；latest 内置 BtbN 静态 ffmpeg）
├── docker-compose.yml            # Compose 编排（downloads + config 卷）
├── requirements.txt              # Python 依赖（yt-dlp 由 Dockerfile 装 nightly）
├── assets/readme/                # 文档配图（纯 SVG，可手改）
├── .github/workflows/
│   └── build-image.yml           # 定时构建并推送 GHCR
├── web/                          # 前端源码（React + HeroUI + Vite）
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── api.js
│       └── components/
│           ├── DownloadForm.jsx
│           ├── CookiePanel.jsx
│           ├── ProxyPanel.jsx
│           ├── TaskList.jsx
│           └── FileList.jsx
├── app/
│   ├── main.py                   # FastAPI 入口 + 全部路由
│   ├── downloader.py             # 任务调度、并发、进度、代理、Cookie
│   ├── tasklog.py                # 日志收集、凭证脱敏与失败摘要
│   ├── store.py                  # 任务记录持久化（SQLite，损坏自动隔离）
│   ├── logconfig.py              # 运行日志落盘与轮转（LOG_FILE 等）
│   ├── accesslog.py              # access log 降噪（轮询与健康检查）
│   ├── serve.py                  # 容器入口：配好日志再启动 uvicorn
│   └── static/                   # 前端构建产物（vite 自动输出，勿手改）
└── tests/
    ├── test_tasklog.py           # 日志脱敏与摘要
    ├── test_task_lifecycle.py    # 任务删除与目录清理策略
    ├── test_resume_and_proxy.py  # 断点续传接管与代理地址规范化
    ├── test_formats.py           # 输出格式预设的完整性
    ├── test_chunk_size.py        # HTTP_CHUNK_SIZE 单位解析
    ├── test_progress.py          # 多流进度累计与后期处理阶段
    ├── test_task_store.py        # 任务持久化、坏库隔离与重启恢复
    ├── test_access_log.py        # 轮询日志过滤
    ├── test_cookie_upload.py     # Cookie 上传限制与 app.main 路由
    ├── test_dockerfile.py        # 健康检查/ compose 端口跟随 PORT
    └── test_log_config.py        # 日志落盘与轮转
```
