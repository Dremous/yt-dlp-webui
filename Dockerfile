# ==========================================================================
# yt-dlp WebUI - 多阶段构建
#
# 阶段一：构建 HeroUI 前端（node 构建产物输出到 app/static）
# 阶段二：后端运行时（python + ffmpeg + 静态前端）
# ==========================================================================

# ---------- 阶段一：前端构建 ----------
FROM node:20-alpine AS web-builder
WORKDIR /app/web

# 先拷贝依赖清单，利用 Docker 层缓存
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

# 拷贝源码并构建（vite 输出到 ../app/static）
COPY web/ ./
RUN npm run build

# ---------- 阶段二：后端运行时 ----------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOWNLOAD_DIR=/downloads \
    COOKIE_FILE=/config/cookies.txt

# ffmpeg：用于视频合并与音频转码（mp3 提取必需）
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /downloads /config

WORKDIR /app

# 后端依赖（yt-dlp 不锁版本：构建时始终拉取最新，随镜像定期刷新）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 后端代码 + 构建好的前端产物
COPY app ./app
COPY --from=web-builder /app/app/static ./app/static

EXPOSE 8080

# 卷：下载文件与 Cookie 持久化
VOLUME /downloads /config

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
