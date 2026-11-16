# ==========================================================================
# yt-dlp WebUI - 多阶段构建
#
# 阶段一：构建 HeroUI 前端（node 构建产物输出到 app/static）
# 阶段二：取 JavaScript 运行时（deno，供 yt-dlp 的 EJS 使用）
# 阶段三：后端运行时（python + ffmpeg + deno + 静态前端）
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

# ---------- 阶段三：后端运行时 ----------
FROM python:3.13-slim

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

# ffmpeg：用于视频合并与音频转码（mp3 提取必需）；gosu：入口脚本降权用
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        gosu \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /downloads /config \
    && groupadd -r app \
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

# 入口脚本：root 启动时先修正持久卷归属，再 gosu 到 app 用户执行 CMD。
# 下载/配置文件由宿主机 bind mount 时，首次创建目录通常属于 root；
# 没有这一步，非 root 进程会无法写入 ./downloads 和 ./config。
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chown -R app:app /app /downloads /config /home/app

# 镜像标签版本（构建时由 workflow 注入 DATE-SHORT_SHA），供「关于」界面显示。
# 注意：ARG/ENV 必须放在所有重量级依赖安装之后。这个值每次构建都会变化，
# 如果放在顶部，会让后续 apt / pip / npm 层全部失效，GHA 缓存几乎无法命中。
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# 端口由运行时 PORT 决定，不写静态 EXPOSE（写死 8080 会与自定义 PORT 不一致）。
# 发布端口请使用 `docker run -p 8080:8080` 或 compose 的 ports 映射。

# 健康检查：直接复用 serve.py 的 _port()，非法 PORT 也和应用一样回退 8080。
# 不用 curl 里的 ${PORT:-8080}，否则 PORT=abc 时应用监听 8080、探活却请求 abc。
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "from app.serve import _port; import http.client, sys; c = http.client.HTTPConnection('127.0.0.1', _port(), timeout=5); c.request('GET', '/api/formats'); r = c.getresponse(); sys.exit(0 if r.status < 400 else 1)"

# 卷：下载文件与配置（Cookie / 代理）持久化
VOLUME /downloads /config

# 入口脚本以 root 修正挂载目录归属后，立即用 gosu 降到 app 用户；
# 实际运行的 uvicorn 进程不是 root。日志由 app 内的 setup_logging 配置。
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "app.serve"]
