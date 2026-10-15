# yt-dlp WebUI (Docker)

一个自带 Web 界面的 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 下载服务，前端基于 **HeroUI（React）** 构建。
在浏览器里粘贴链接即可下载视频 / 音频，支持实时进度、后台并发任务、**登录 Cookie 导入**和已下载文件管理。

可通过 **GitHub Actions 定时自动构建最新 yt-dlp nightly 镜像**并推送到 GitHub Container Registry（GHCR）。

## 特性

- **HeroUI Web 界面**：现代 React UI 组件库，无需命令行
- **三种输出格式**：MP4 视频、最佳画质（原始格式）、MP3 音频（192kbps）
- **实时进度**：百分比、速度、剩余时间
- **并发任务**：多个下载并行，互不阻塞
- **文件管理**：在网页里直接下载 / 删除已下载文件
- **登录 Cookie 导入**：上传 `cookies.txt`（浏览器插件导出），用于下载私享 / 会员 / 年龄受限内容
- **安全设计**：URL 全部走 yt-dlp Python API（无 shell 拼接），文件名做了路径穿越防护与 HTML 转义
- **自动更新**：yt-dlp 每次构建从 GitHub nightly-builds 拉取最新 nightly，配合定时重建始终跟随最新开发版

## 技术栈

| 组件 | 说明 |
|------|------|
| 前端 | React 18 + HeroUI + Tailwind CSS + Vite |
| 后端 | Python 3.13 + FastAPI + Uvicorn |
| 下载引擎 | yt-dlp（Python API，每次构建拉取 nightly） |
| 容器 | 多阶段镜像（node 构建前端 → python 运行），内置 ffmpeg |
| CI | GitHub Actions 定时构建并推送 GHCR |

## 快速开始

### 方式一：直接使用 GitHub 自动构建的镜像（推荐）

在 GitHub Actions 中配置好 workflow 后（见下文），镜像会定期推送到 GHCR：

```bash
docker run -d --name yt-dlp-webui \
  -p 8080:8080 \
  -v "$PWD/downloads:/downloads" \
  -v "$PWD/config:/config" \
  ghcr.io/dremous/yt-dlp-webui:latest
```

### 方式二：本地 Docker Compose 构建

```bash
cd yt-dlp-webui
docker compose up -d --build
```

### 方式三：仅 Docker

```bash
docker build -t yt-dlp-webui .
docker run -d --name yt-dlp-webui \
  -p 8080:8080 \
  -v "$PWD/downloads:/downloads" \
  -v "$PWD/config:/config" \
  --restart unless-stopped \
  yt-dlp-webui
```

启动后浏览器访问 **http://localhost:8080**。
下载文件保存在 `./downloads`，Cookie 保存在 `./config`（均可持久化）。

## 使用登录 Cookie

下载需要登录的站点（如 YouTube 会员 / 私享视频）时，先在浏览器安装 Cookie 导出插件
（如 "Get cookies.txt LOCALLY"），导出目标站点的 `cookies.txt`，然后在 WebUI 的
**「登录 Cookie」** 卡片中上传。之后所有下载请求都会自动携带这些 Cookie。
可随时上传覆盖或清除。

支持格式：Netscape `cookies.txt` 或 JSON（yt-dlp 的 `cookiefile` 选项）。

## GitHub Actions 自动构建最新版

工作流位于 `.github/workflows/build-image.yml`，三种触发方式：

| 触发 | 时机 |
|------|------|
| `schedule` | 每周三 02:00 UTC（北京时间每周三 10:00） |
| `workflow_dispatch` | 在 Actions 页面手动点击 "Run workflow" |
| `push` | 推送 `main` 分支或打 `v*` 标签 |

构建时从 GitHub `yt-dlp-nightly-builds` 源码安装 `yt-dlp`（模块名仍为 `yt_dlp`，代码零改动），因此每次构建的镜像都内置最新 nightly 构建。
产物推送到 **GHCR**，标签为 `latest` 和 `run_number`（如 `ghcr.io/<owner>/<repo>:latest`）。

> 前置设置：GitHub 仓库 `Settings → Packages` 中启用 GHCR；首次使用时需为镜像设置可见性。
> 无需额外密钥 —— workflow 使用内置的 `GITHUB_TOKEN` 自动完成 GHCR 登录。

## 本地开发（不带 Docker）

```bash
# 1) 后端
pip install -r requirements.txt          # 需系统装有 ffmpeg
pip install -U yt-dlp                     # yt-dlp 不在 requirements.txt（镜像内由 Dockerfile 安装 nightly）
DOWNLOAD_DIR=/tmp/ytdl-downloads uvicorn app.main:app --reload --port 8080

# 2) 前端（另开终端，构建产物输出到 app/static）
cd web && npm install && npm run dev      # 开发服务器 http://localhost:5173，代理到 8080
```

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 界面 |
| GET | `/api/formats` | 可用输出格式 |
| POST | `/api/download` | 提交下载任务，body: `{"url": "...", "format": "mp4"}` |
| GET | `/api/tasks?limit=N` | 任务列表（含进度；可选 limit 限制条数，默认 30，上限 200） |
| POST | `/api/tasks/{task_id}/cancel` | 取消排队/下载中的任务（失败/已取消任务不可取消） |
| GET | `/api/files` | 已下载文件列表 |
| GET | `/files/{filename:path}` | 下载指定文件（相对路径，含任务子目录） |
| DELETE | `/api/files/{filename:path}` | 删除指定文件 |
| POST | `/api/cookies` | 上传 Cookie 文件（multipart，字段 `file`） |
| GET | `/api/cookies/status` | 查询 Cookie 状态 |
| DELETE | `/api/cookies` | 清除 Cookie |

## 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DOWNLOAD_DIR` | `/downloads` | 下载文件存放目录 |
| `COOKIE_FILE` | `/config/cookies.txt` | Cookie 文件路径 |
| `-p 8080:8080` | - | 对外映射端口 |

## 项目结构

```
.
├── Dockerfile                    # 多阶段构建（node 前端 → python 后端）
├── docker-compose.yml            # Compose 编排（downloads + config 卷）
├── requirements.txt              # Python 依赖（yt-dlp 改由 Dockerfile 装 nightly）
├── .github/workflows/
│   └── build-image.yml           # 定时自动构建并推送 GHCR
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
│           ├── TaskList.jsx
│           └── FileList.jsx
└── app/
    ├── main.py                   # FastAPI 入口 + 路由（含 Cookie API）
    ├── downloader.py             # yt-dlp 下载任务管理（并发 + 进度 + cookiefile）
    └── static/                   # 前端构建产物（vite 自动输出，勿手改）
```

## 说明

- 请遵守目标网站的条款与版权规定，仅下载你有权下载的内容。
- 使用 Cookie 时注意保密：`./config` 目录包含你的登录凭证，不要提交到公开仓库（已加入 `.gitignore`）。
- 部分站点可能需要额外配置（如代理），本项目保持开箱即用的简洁默认。
