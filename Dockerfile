FROM node:22-bookworm-slim AS web-builder

WORKDIR /build/web

# 前端构建依赖；lockfile 变化时才会重新安装
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/ ./
RUN npm run build


FROM python:3.12-slim-bookworm

WORKDIR /app

# ffmpeg 用于音视频合并；curl 保留给运维排查
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

# 先装依赖再拷源码，最大化利用 Docker 层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY --from=web-builder /build/web/dist ./web/dist

# 以非 root 用户运行；uid/gid 固定，便于宿主机卷权限调整
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid 1000 --create-home app \
 && mkdir -p /downloads /config \
 && chown -R app:app /app /downloads /config

USER app

ENV DOWNLOAD_DIR=/downloads \
    COOKIE_FILE=/config/cookies.txt \
    PROXY_FILE=/config/proxy.txt

EXPOSE 8080

# 探针复用应用自身的端口解析（app.serve._port），保证非法 PORT 时
# 探针和监听端口一起回退到 8080，不会出现探针打 8080、应用监听 9000 的错位。
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import http.client, sys; from app.serve import _port; p = _port(); c = http.client.HTTPConnection('127.0.0.1', p, timeout=3); c.request('GET', '/api/formats'); sys.exit(0 if c.getresponse().status < 500 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
