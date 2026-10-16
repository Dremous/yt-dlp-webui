# ==========================================================================
# yt-dlp WebUI - 多阶段构建
#
# 阶段一：构建 HeroUI 前端（node 构建产物输出到 app/static）
# 阶段二：后端运行时（python + ffmpeg + 静态前端）
# ==========================================================================

# ---------- 阶段一：前端构建 ----------
# --platform=$BUILDPLATFORM：前端产物是纯静态 JS/HTML/CSS，与目标架构无关。
# 在 runner 原生架构（amd64）上跑 npm ci / vite build，避免 arm64 交叉编译时
# QEMU 模拟 node 极慢甚至卡死（此前 npm ci 卡在 [linux/arm64 web-builder] 的根因）。
FROM --platform=$BUILDPLATFORM node:20-alpine AS web-builder
WORKDIR /app/web

# 先拷贝依赖清单，利用 Docker 层缓存
# 直接用 npm ci（lock 精确安装，不再回退 npm install 引入不一致；保留 audit）
COPY web/package*.json ./
RUN npm ci --no-fund

# 拷贝源码并构建（vite 输出到 ../app/static）
COPY web/ ./
RUN npm run build

# ---------- 阶段二：后端运行时 ----------
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOWNLOAD_DIR=/downloads \
    COOKIE_FILE=/config/cookies.txt

# 镜像标签版本（构建时由 workflow 注入 DATE-SHORT_SHA），供「关于」界面显示
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# ffmpeg：用于视频合并与音频转码（mp3 提取必需）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /downloads /config

WORKDIR /app

# 后端基础依赖（fastapi / uvicorn）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# yt-dlp：每次构建从 GitHub nightly-builds 拉取最新 nightly 源码安装
RUN pip install --no-cache-dir --upgrade \
    "yt-dlp @ https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"

# 后端代码 + 构建好的前端产物
COPY app ./app
COPY --from=web-builder /app/app/static ./app/static

EXPOSE 8080

# 健康检查：curl 探活 API 端点
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fs http://127.0.0.1:8080/api/formats || exit 1

# 卷：下载文件与 Cookie 持久化
VOLUME /downloads /config

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
