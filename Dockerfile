# ==========================================================================
# yt-dlp WebUI - 多阶段构建
#
# 阶段一：构建 HeroUI 前端（node 构建产物输出到 app/static）
# 阶段二：取 JavaScript 运行时（deno，供 yt-dlp 的 EJS 使用）
# 阶段三：下载 BtbN GPL 静态 ffmpeg（唯一发布变体使用）
# 阶段四：后端运行时公共层（python + deno + 静态前端，不含 ffmpeg）
# 阶段五：latest（BtbN GPL 静态 ffmpeg；唯一发布变体）
#
# 默认 build target 是最后一个阶段 latest。
# ==========================================================================

# 必须在第一个 FROM 之前声明：只有全局作用域的 ARG 才能参与 FROM 的变量替换。
# 若 denoland/deno 上该 tag 不存在，改成 bin-latest 即可。
ARG DENO_VERSION=2.9.7

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

# ---------- 阶段二：JavaScript 运行时 ----------
# yt-dlp 用 EJS 解决 YouTube 的 JS 挑战（n 参数 / signature），必须配一个外部运行时。
# 官方推荐 deno 且默认启用；这里从官方 bin 镜像取静态二进制，避免 apt 源里没有 deno。
# 多架构构建时按目标平台自动选对应架构的二进制（amd64 / arm64 均有）。
FROM denoland/deno:bin-${DENO_VERSION} AS deno-bin

# ---------- 阶段三：BtbN GPL 静态 ffmpeg（唯一发布变体使用） ----------
# BtbN/FFmpeg-Builds 的 GPL 构建把 FFmpeg 库静态编进 ffmpeg/ffprobe，运行时
# 只依赖 Debian 自带的 glibc。CI 每次构建会解析 latest release 中最高版本的
# nX.Y 稳定分支资产；FFMPEG_BUILDS_REFRESH 用 run id 强制刷新，避免 Docker
# 因为资产名不变而命中旧层、继续使用上一次下载的二进制。
FROM debian:bookworm-slim AS ffmpeg-static
ARG TARGETARCH
ARG FFMPEG_BUILDS_VERSION=9.0
ARG FFMPEG_BUILDS_REFRESH=local

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    echo "BtbN GPL ffmpeg refresh: ${FFMPEG_BUILDS_REFRESH}"; \
    case "${TARGETARCH}" in \
      amd64) ARCH=linux64 ;; \
      arm64) ARCH=linuxarm64 ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    ASSET="ffmpeg-n${FFMPEG_BUILDS_VERSION}-latest-${ARCH}-gpl-${FFMPEG_BUILDS_VERSION}.tar.xz"; \
    mkdir -p /opt/ffmpeg; \
    curl -fL --retry 3 --retry-delay 5 -o "/tmp/${ASSET}" \
      "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/${ASSET}"; \
    tar -xJf "/tmp/${ASSET}" --strip-components=1 -C /opt/ffmpeg; \
    rm -f "/tmp/${ASSET}"; \
    /opt/ffmpeg/bin/ffmpeg -version > /opt/ffmpeg/version.txt; \
    /opt/ffmpeg/bin/ffprobe -version >> /opt/ffmpeg/version.txt; \
    cat /opt/ffmpeg/version.txt

# ---------- 阶段四：后端运行时公共层 ----------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOWNLOAD_DIR=/downloads \
    COOKIE_FILE=/config/cookies.txt \
    PROXY_FILE=/config/proxy.txt \
    DENO_NO_UPDATE_CHECK=1

# 运行日志落盘位置（/config 是持久卷）；置 none 则只走 docker logs
ENV LOG_FILE=/config/webui.log

# 容器监听端口；健康检查跟随同一变量，避免自定义 PORT 后探活仍打 8080
ENV PORT=8080

# 降权用户/组的默认值。运行时可用 -e PUID=1000 -e PGID=1000 覆盖，
# 入口脚本会按新值修正挂载目录归属，再 gosu 到该 UID/GID。
ENV PUID=10001 \
    PGID=10001

# 容器内本地时区；影响日志时间戳和任务库备份文件名。
# 运行时可用 -e TZ=Asia/Tokyo 覆盖，入口脚本会校验 IANA 时区名。
ENV TZ=Asia/Shanghai

# gosu：入口脚本降权用；tzdata：提供 /usr/share/zoneinfo，供 TZ 变量解析时区。
# ffmpeg 不在这里安装：最终 latest 阶段从 ffmpeg-static 复制静态二进制。
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gosu \
        tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /downloads /config \
    && groupadd -r -g 10001 app \
    && useradd -r -g app -u 10001 -d /home/app -m app

# 降权后的进程需要可写的 HOME（Deno / yt-dlp 会在这里放缓存）
ENV HOME=/home/app

WORKDIR /app

# deno：yt-dlp 的 JS 运行时。构建期执行一次 --version，
# 若把错架构的二进制拷进来会在这里立刻失败，而不是等到用户下载时才暴露。
COPY --from=deno-bin /deno /usr/local/bin/deno
RUN deno --version

# 后端基础依赖（fastapi / uvicorn）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# yt-dlp：每次构建从 GitHub nightly-builds 拉取最新 nightly 源码安装
RUN pip install --no-cache-dir --upgrade \
    "yt-dlp @ https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"

# yt-dlp-ejs：EJS 挑战求解脚本。官方要求与 JS 运行时配套安装，缺了它 YouTube
# 提取会退化为「部分格式缺失」。用 tarball URL 安装 yt-dlp 不会解析 [default] extras，
# 因此这里显式单独安装，并立即验证能被导入（装了但导入失败是常见的失败模式）。
RUN pip install --no-cache-dir --upgrade yt-dlp-ejs \
    && python -c "import yt_dlp_ejs" && echo "yt-dlp-ejs OK"

# 后端代码 + 构建好的前端产物
COPY app ./app
COPY --from=web-builder /app/app/static ./app/static

# 入口脚本：root 启动时先修正持久卷归属，再 gosu 到 PUID:PGID 执行 CMD。
# 下载/配置文件由宿主机 bind mount 时，首次创建目录通常属于 root；
# 没有这一步，非 root 进程会无法写入 ./downloads 和 ./config。
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chown -R app:app /app /downloads /config /home/app

# 端口由运行时 PORT 决定，不写静态 EXPOSE（写死 8080 会与自定义 PORT 不一致）。
# 发布端口请使用 `docker run -p 8080:8080` 或 compose 的 ports 映射。

# 健康检查：直接复用 serve.py 的 _port()，非法 PORT 也和应用一样回退 8080。
# 不用 curl 里的 ${PORT:-8080}，否则 PORT=abc 时应用监听 8080、探活却请求 abc。
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "from app.serve import _port; import http.client, sys; c = http.client.HTTPConnection('127.0.0.1', _port(), timeout=5); c.request('GET', '/api/formats'); r = c.getresponse(); sys.exit(0 if r.status < 400 else 1)"

# 卷：下载文件与配置（Cookie / 代理）持久化
VOLUME /downloads /config

# 入口脚本以 root 修正挂载目录归属后，立即用 gosu 降到 PUID:PGID；
# 实际运行的 uvicorn 进程不是 root。日志由 app 内的 setup_logging 配置。
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "app.serve"]

# ---------- 阶段五：latest（BtbN GPL ffmpeg，唯一发布变体） ----------
# 复制 BtbN 的 ffmpeg/ffprobe 和随包 LICENSE.txt；ffmpeg 与 ffprobe 都保留，
# 避免 yt-dlp 的音频探测和元数据能力出现回归。构建期执行 --version，
# 错架构/缺 glibc 依赖会立即失败。
FROM runtime AS latest-base
RUN mkdir -p /usr/share/doc/ffmpeg
COPY --from=ffmpeg-static /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-static /opt/ffmpeg/bin/ffprobe /usr/local/bin/ffprobe
COPY --from=ffmpeg-static /opt/ffmpeg/LICENSE.txt /usr/share/doc/ffmpeg/LICENSE
COPY --from=ffmpeg-static /opt/ffmpeg/version.txt /usr/share/doc/ffmpeg/version.txt
RUN ffmpeg -version >/dev/null && ffprobe -version >/dev/null

# 版本号单独放最终元数据阶段：如果和静态二进制放同一阶段，APP_VERSION 每次
# 构建都会变，BuildKit 会把这一阶段的重活一起判为 cache miss。最终阶段只写
# ENV，不复制/安装任何东西。latest 放在最后，docker build 不带 --target 时
# 默认构建它。
FROM latest-base AS latest
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
