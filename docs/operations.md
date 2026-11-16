# 部署与运维

[返回 README](../README.md)

## 自动构建（GitHub Actions）

工作流位于 `.github/workflows/build-image.yml`，三种触发方式：

| 触发 | 时机 |
|---|---|
| `schedule` | 每周日 00:00 UTC（北京时间周日 08:00） |
| `workflow_dispatch` | 在 Actions 页面手动点击 "Run workflow" |
| `push` | 推送 `main` 分支或打 `v*` 标签 |

构建时从 GitHub `yt-dlp-nightly-builds` 源码安装 `yt-dlp`（模块名仍为 `yt_dlp`，代码零改动），
因此每次构建的镜像都内置最新 nightly。产物推送到 **GHCR**，标签为：

| 变体 | 标签 |
|---|---|
| latest（Debian apt ffmpeg） | `latest`、`DATE`、`DATE-SHORT_SHA` |
| slim（静态 ffmpeg） | `slim`、`DATE-slim`、`DATE-SHORT_SHA-slim` |

两个变体共用同一套 Python/Deno/应用层，CI 用独立 registry cache tag 分别写入；
构建时会读取另一变体的 cache 复用公共层，但不写入对方的 tag。缓存 tag 只按
ref/变体区分，不包含 `DATE` 或 commit SHA；`APP_VERSION` 只写在最终阶段，
避免每次构建都改变公共 `runtime` 的 image config，导致 apt / 静态二进制层
被判定为 cache miss。

### latest 与 slim 的区别

- **latest**：保持原有行为，`apt-get install ffmpeg`，包含 Debian 的完整编解码组合。
- **slim**：用固定版本的 `static-ffmpeg` 二进制（johnvansickle 7.0.2，GPLv3）替代 apt ffmpeg，
  同时复制 `ffmpeg` 与 `ffprobe`，避免 yt-dlp 的音频探测/元数据能力退化。
  下载资产固定为 `eugeneware/ffmpeg-static` 的 `b6.1.1` Release，四个资产的 SHA256 写死在
  Dockerfile 中；PUID/PGID、TZ、非 root 运行、YouTube EJS 全部保留。

slim 的代价是构建时依赖 GitHub Release 下载；如果上游资产被替换，SHA256 校验会让构建失败，
需要人工确认后更新 Dockerfile 中的版本与校验值。GPLv3 许可证和上游 README 会复制到
镜像内的 `/usr/share/doc/ffmpeg/`。

> 前置设置：GitHub 仓库 `Settings → Packages` 中启用 GHCR；首次使用时需为镜像设置可见性。
> 无需额外密钥——workflow 使用内置的 `GITHUB_TOKEN` 自动完成 GHCR 登录。
> 可选配置 `secrets.WEBHOOK_URL`（飞书 / 企业微信 / 钉钉机器人），构建失败时推送告警。

## 运行日志落盘

`docker logs` 只保留 stdout，容器一删就没了。镜像默认把同一份日志写到
`/config/webui.log`（随 `./config` 卷持久化），并按 10MB × 5 份自动轮转：

```bash
tail -f config/webui.log                                  # 宿主机上直接看
docker exec yt-dlp-webui tail -n 100 /config/webui.log    # 或进容器看
```

```
2026-11-15 19:58:01 INFO     uvicorn.error: Application startup complete.
2026-11-15 19:58:03 INFO     uvicorn.access: 192.168.0.2:1 - "POST /api/download HTTP/1.1" 200
```

- 想留久一点：`LOG_MAX_MB=50 LOG_BACKUPS=10`
- 完全不想落盘：`LOG_FILE=none`
- 只写文件、不要 `docker logs`：`LOG_CONSOLE=0`
- 想看更细：`LOG_LEVEL=DEBUG`
- 日志里每 30 秒一条 `GET /api/formats` 是 Docker 的 HEALTHCHECK 探活，已默认静音（失败时仍会打印）
- 容器入口与本地 `uvicorn app.main:app` 都读同一组 `LOG_*` 变量（文件在本地同样生效）

> 也可以交给 Docker 自己轮转：`--log-opt max-size=10m --log-opt max-file=5`。
> 两者不冲突——文件那份方便拷走，`docker logs` 那份方便 `docker logs -f`。

## 本地开发

```bash
# 1) 后端（Windows 下 /downloads、/config 等默认路径不可用，需显式覆盖）
pip install -r requirements.txt          # 需系统装有 ffmpeg
pip install -U yt-dlp                     # yt-dlp 不在 requirements.txt（镜像内由 Dockerfile 装 nightly）
DOWNLOAD_DIR=/tmp/ytdl-downloads \
COOKIE_FILE=/tmp/ytdl-config/cookies.txt \
PROXY_FILE=/tmp/ytdl-config/proxy.txt \
uvicorn app.main:app --reload --port 8080

# 2) 前端（另开终端，构建产物输出到 app/static）
cd web && npm install && npm run dev      # 开发服务器 http://localhost:5173，代理到 8080

# 3) 单元测试（只用标准库，不需要安装 yt-dlp）
python -m unittest discover -s tests -v
```

改了前端要 `cd web && npm run build` 才会在 `:8080` 生效。

## 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DOWNLOAD_DIR` | `/downloads` | 下载文件存放目录 |
| `COOKIE_FILE` | `/config/cookies.txt` | Cookie 文件路径 |
| `PROXY_FILE` | `/config/proxy.txt` | 代理地址文件路径（一行 URL，由界面写入） |
| `HTTP_CHUNK_SIZE` | `512M` | 单块大小，支持 `512M` / `1024K` / 字节数；`0` = 不分块 |
| `TASKS_DB` | `<COOKIE_FILE 所在目录>/tasks.db` | 任务记录库（SQLite）路径，默认与 Cookie 同卷；库不可写时删除会写同名 `.deleted` 墓碑 |
| `ACCESS_LOG_QUIET` | `1` | 置 `0` 恢复每条请求的 access log；默认静音成功的 `/api/tasks`、`/api/files` 轮询与 `/api/formats` 健康检查 |
| `LOG_FILE` | `/config/webui.log` | 运行日志文件路径；置 `none` 则只走 `docker logs` |
| `LOG_LEVEL` | `INFO` | 日志级别：`DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_MAX_MB` | `10` | 单个日志文件上限（MB），超出后轮转；`0` = 不轮转 |
| `LOG_BACKUPS` | `5` | 保留的历史日志文件个数 |
| `LOG_CONSOLE` | `1` | 置 `0` 则只写文件，不再打印到 `docker logs` |
| `PORT` | `8080` | 容器内监听端口；健康检查与 compose 端口映射会跟随该变量 |
| `TZ` | `Asia/Shanghai` | 容器内本地时区（IANA 名称）；影响日志时间戳与任务库损坏备份文件名 |
| `PUID` | `10001` | 容器内进程与挂载目录的 UID；设为宿主机用户（如 `1000`）可避免下载文件归属错位 |
| `PGID` | `10001` | 容器内进程与挂载目录的 GID；通常与 `PUID` 一起设置 |
| `WEBUI_ALLOWED_HOSTS` | 空 | 额外放行的 Host（逗号分隔，如自定义域名）；localhost、私网 IP、`host.docker.internal` 默认放行 |

对外端口默认 8080，可用 `PORT` 覆盖；容器内监听、健康检查与 compose 映射会一起跟随。

`TZ` 默认 `Asia/Shanghai`，可改为 `UTC`、`Asia/Tokyo` 等 IANA 时区名。它影响服务端日志
时间戳和任务库损坏时的备份文件名；文件列表里的下载时间由浏览器按本地时区格式化。
入口脚本会检查 `/usr/share/zoneinfo`，非法时区名会拒绝启动，避免静默回退成 UTC。

`PUID` / `PGID` 默认为 `10001:10001`。如果宿主机用户 UID/GID 是 `1000`，在 `.env` 或
compose 环境里设置 `PUID=1000`、`PGID=1000`，重启容器后入口脚本会先递归修正
`/downloads`、`/config` 与 `/home/app` 的归属，再以该 UID/GID 启动 uvicorn。
只接受 1-2147483647 的整数；设为 `0` 或非数字会拒绝启动，避免意外回到 root。
用 `docker run --user` 直接指定用户时，入口脚本不会执行 chown，需要自行保证挂载目录可写。

`docker-compose.yml` 的 `BIND_ADDR` 控制宿主机绑定网卡，默认 `0.0.0.0` 以保持现有局域网访问；
建议设为具体 LAN IP（如 `192.168.1.10`）以避开 VPN/公网网卡，设为 `127.0.0.1` 则只允许本机访问。
自定义域名访问时还要把域名写入 `WEBUI_ALLOWED_HOSTS`，否则 Host 校验会返回 400。

`PORT` 必须是 1-65535 的整数。`docker run` 下非法值（如 `abc`）会让应用回退到 8080，健康检查也走同一回退；`docker compose` 会在解析端口映射时直接报错（fail-fast），不会启动出“应用监听 8080、映射却是 abc:abc”的不一致容器。
