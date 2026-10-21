# yt-dlp WebUI (Docker)

一个自带 Web 界面的 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 下载服务，前端基于 **HeroUI（React）** 构建。
在浏览器里粘贴链接即可下载视频 / 音频，支持实时进度、后台并发任务、**登录 Cookie 导入**、
**网络代理配置**和已下载文件管理。

可通过 **GitHub Actions 定时自动构建最新 yt-dlp nightly 镜像**并推送到 GitHub Container Registry（GHCR）。

## 特性

- **HeroUI Web 界面**：现代 React UI 组件库，无需命令行
- **三种输出格式**：MP4 视频、最佳画质（原始格式）、MP3 音频（192kbps）
- **实时进度**：百分比、速度、剩余时间
- **并发任务**：多个下载并行，互不阻塞
- **文件管理**：在网页里直接下载 / 删除已下载文件
- **登录 Cookie 导入**：上传 `cookies.txt`（浏览器插件导出），用于下载私享 / 会员 / 年龄受限内容
- **网络代理**：界面里配置 HTTP / SOCKS5 代理并一键测试连通性，解决国内无法直连 YouTube 的问题
- **失败可诊断**：下载失败时保留 yt-dlp 的完整日志（代理密码自动脱敏），界面直接显示关键错误行
- **安全设计**：URL 全部走 yt-dlp Python API（无 shell 拼接），文件名做了路径穿越防护与 HTML 转义
- **自动更新**：yt-dlp 每次构建从 GitHub nightly-builds 拉取最新 nightly，配合定时重建始终跟随最新开发版

## 技术栈

| 组件 | 说明 |
|------|------|
| 前端 | React 18 + HeroUI + Tailwind CSS + Vite |
| 后端 | Python 3.13 + FastAPI + Uvicorn |
| 下载引擎 | yt-dlp（Python API，每次构建拉取 nightly）+ deno（EJS 挑战求解） |
| 容器 | 多阶段镜像（node 构建前端 → python 运行），内置 ffmpeg 与 deno |
| CI | GitHub Actions 定时构建并推送 GHCR |

## 快速开始

### 方式一：直接使用 GitHub 自动构建的镜像（推荐）

在 GitHub Actions 中配置好 workflow 后（见下文），镜像会定期推送到 GHCR：

```bash
docker run -d --name yt-dlp-webui \
  -p 8080:8080 \
  -v "$PWD/downloads:/downloads" \
  -v "$PWD/config:/config" \
  --add-host=host.docker.internal:host-gateway \
  ghcr.io/dremous/yt-dlp-webui:latest
```

### 方式二：Docker Compose 部署

```bash
cd yt-dlp-webui
docker compose up -d
```

### 方式三：仅 Docker

```bash
docker build -t yt-dlp-webui .
docker run -d --name yt-dlp-webui \
  -p 8080:8080 \
  -v "$PWD/downloads:/downloads" \
  -v "$PWD/config:/config" \
  --add-host=host.docker.internal:host-gateway \
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

## 国内网络：配置代理

大陆网络无法直连 YouTube（以及 Google 系域名），不带代理时任务会以
`Unable to download webpage` / 连接超时之类的错误失败。在 WebUI 的
**「网络代理」** 卡片里填好代理地址并点 **「测试连通」** 确认能通即可。

### 前提：让代理允许局域网连接

容器与宿主机网络是隔离的，代理必须监听非 loopback 地址，否则容器连不上：

- Clash / Clash Verge / Mihomo：打开 **「允许局域网连接」**（配置文件里的 `allow-lan: true`）
- v2ray / sing-box：监听地址设为 `0.0.0.0`
- 同时放行服务器防火墙上的代理端口（`7890` / `7891` 等）

> Linux / NAS 上这一步不做，页面上会一直提示「连接超时」。这是最常见的失败原因。

### 代理地址怎么填

| 代理所在位置 | 填法 |
|---|---|
| 就在同一台机器（宿主机）上 | `http://host.docker.internal:7890` |
| 是另一个 Docker 容器 | `http://<容器名>:7890`（需与本项目在同一 docker 网络） |
| 在局域网其他机器 / 软路由上 | `http://192.168.1.2:7890` |

- **不要填 `127.0.0.1`**——容器里的它指向容器自己，不是宿主机。填错时页面会直接给出提示。
- 只填 `主机:端口` 也可以，缺省按 `http://` 处理。
- 支持 `http` / `https` / `socks5` / `socks5h`；`socks5h` 由代理端解析域名，容器场景更省事。

### 排错对照

| 测试结果 | 通常原因 |
|---|---|
| 连接被拒绝 | 地址/端口写错，或代理没在监听该端口 |
| 连接超时 | 代理未开启「允许局域网连接」，或防火墙拦了端口 |
| SOCKS 代理不可用 | `socks5` 需要 PySocks，官方镜像已内置；自建环境请 `pip install PySocks` |
| 连通正常但下载仍失败 | 目标站点本身需要登录凭证，见上一节的 Cookie |

## 下载失败怎么排查

失败的任务会在下载目录里留下完整日志，其中代理地址的账号密码已自动脱敏：

```
downloads/<任务ID>/yt-dlp.log
```

同时，失败时最后 30 行日志会打印到容器日志，可以直接查看：

```bash
docker logs --tail 100 yt-dlp-webui
```

界面上的错误行取自日志里最后一条 `ERROR:`，末尾附上日志的相对路径。

> 日志只在失败时落盘，下载成功不会产生 `.log` 文件，`/api/files` 也不会把它列为下载成品。
> 内存中最多保留 2000 行、单行截断到 2000 字符，避免超长签名 URL 撑爆内存。

### 关于 JavaScript 运行时

YouTube 的播放地址带 JS 挑战（`n` 参数签名），yt-dlp 需要外部 JS 运行时才能解开。
镜像已内置 **deno** 与配套的 **yt-dlp-ejs** 脚本包，无需额外配置。

若日志里出现 `JS runtimes: none` 或 `[jsc] JS Challenge Providers: ... (unavailable)`，
说明镜像构建不完整（缺少上面两者），YouTube 提取会退化为「部分格式缺失」。

## GitHub Actions 自动构建最新版

工作流位于 `.github/workflows/build-image.yml`，三种触发方式：

| 触发 | 时机 |
|------|------|
| `schedule` | 每周末 00:00 UTC（北京时间每周末 00:00） |
| `workflow_dispatch` | 在 Actions 页面手动点击 "Run workflow" |
| `push` | 推送 `main` 分支或打 `v*` 标签 |

构建时从 GitHub `yt-dlp-nightly-builds` 源码安装 `yt-dlp`（模块名仍为 `yt_dlp`，代码零改动），因此每次构建的镜像都内置最新 nightly 构建。
产物推送到 **GHCR**，标签为 `latest` 和 `run_number`（如 `ghcr.io/<owner>/<repo>:latest`）。

> 前置设置：GitHub 仓库 `Settings → Packages` 中启用 GHCR；首次使用时需为镜像设置可见性。
> 无需额外密钥 —— workflow 使用内置的 `GITHUB_TOKEN` 自动完成 GHCR 登录。

## 本地开发（不带 Docker）

```bash
# 1) 后端（Windows 下 /downloads、/config 等默认路径不可用，需显式覆盖）
pip install -r requirements.txt          # 需系统装有 ffmpeg
pip install -U yt-dlp                     # yt-dlp 不在 requirements.txt（镜像内由 Dockerfile 安装 nightly）
DOWNLOAD_DIR=/tmp/ytdl-downloads \
COOKIE_FILE=/tmp/ytdl-config/cookies.txt \
PROXY_FILE=/tmp/ytdl-config/proxy.txt \
uvicorn app.main:app --reload --port 8080

# 2) 前端（另开终端，构建产物输出到 app/static）
cd web && npm install && npm run dev      # 开发服务器 http://localhost:5173，代理到 8080

# 3) 单元测试（只用标准库，不需要安装 yt-dlp）
python -m unittest discover -s tests -v
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
| GET | `/api/proxy` | 查询代理状态 |
| PUT | `/api/proxy` | 保存代理，body: `{"url": "http://host.docker.internal:7890"}` |
| DELETE | `/api/proxy` | 清除代理，恢复直连 |
| POST | `/api/proxy/test` | 测试代理能否连通 YouTube（body 可传 `{"url": "..."}` 测未保存的地址） |

## 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DOWNLOAD_DIR` | `/downloads` | 下载文件存放目录 |
| `COOKIE_FILE` | `/config/cookies.txt` | Cookie 文件路径 |
| `PROXY_FILE` | `/config/proxy.txt` | 代理地址文件路径（一行 URL，由界面写入） |
| `-p 8080:8080` | - | 对外映射端口 |

## 项目结构

```
.
├── Dockerfile                    # 多阶段构建（node 前端 → deno 运行时 → python 后端）
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
│           ├── ProxyPanel.jsx
│           ├── TaskList.jsx
│           └── FileList.jsx
└── app/
    ├── main.py                   # FastAPI 入口 + 路由（含 Cookie / 代理 API）
    ├── downloader.py             # yt-dlp 下载任务管理（并发 + 进度 + cookiefile + 代理）
    ├── tasklog.py                # yt-dlp 日志收集与凭证脱敏（纯标准库，本地可单测）
    └── static/                   # 前端构建产物（vite 自动输出，勿手改）

tests/
└── test_tasklog.py               # python -m unittest discover -s tests -v
```

## 说明

- 请遵守目标网站的条款与版权规定，仅下载你有权下载的内容。
- 使用 Cookie 时注意保密：`./config` 目录含登录凭证与代理地址（可能带账号密码），
  不要提交到公开仓库（已加入 `.gitignore`）。
- 代理只影响 yt-dlp 的出站请求，不影响浏览器访问 WebUI 本身。
