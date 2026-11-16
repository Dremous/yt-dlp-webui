运行与配置
============

本文档只讲**怎么跑、参数怎么设、状态存哪里**。接口语义见项目根目录 `README.md`，
开发与测试方式见 `docs/development.md`。


容器运行
--------

```bash
docker compose up -d --build
```

默认映射 `8080:8080`。需要换端口时：

```bash
PORT=9000 docker compose up -d
```

`PORT` 必须是 **1–65535 的整数**，不要写 `abc`、`0` 或 `65536`。

- Compose 在启动前做变量替换，非法值会直接报错（fail-fast），不会带着错误的映射启动。
- 应用自身（`app.serve._port()`）在 `PORT` 非法时回退到 `8080`；容器 `HEALTHCHECK` 也调用同一个函数，
  所以探针和监听端口始终一致。

持久化目录：

| 容器路径 | 内容 |
|----------|------|
| `/downloads` | 下载完成的文件 |
| `/config` | Cookie、代理配置、日志、任务库 |

`/config` 中会出现的文件：

| 文件 | 作用 |
|------|------|
| `cookies.txt` | Cookie 上传后的存储位置 |
| `proxy.txt` | 代理地址 |
| `webui.log` | 运行日志（轮转） |
| `tasks.db` | 任务库（SQLite） |
| `tasks.db.deleted` | 任务墓碑，记录已在内存中删除、但任务库降级时未能同步删除的任务 ID；`TaskStore.load()` 读取时会过滤这些 ID |

`tasks.db.deleted` 是降级保护文件，不需要手工编辑。任务库可用时会正常删除数据库行；
数据库删除失败但墓碑可写时，删除仍返回成功，重启后旧行也不会复活。只有墓碑也写不进去时，
内存记录才会保留，避免出现“界面看不到、数据库里还在”的静默数据不一致。


环境变量
--------

| 变量 | 默认 | 说明 |
|------|------|------|
| `DOWNLOAD_DIR` | `/downloads` | 下载输出目录 |
| `COOKIE_FILE` | `/config/cookies.txt` | Cookie 文件路径 |
| `PROXY_FILE` | `/config/proxy.txt` | 代理配置文件路径 |
| `LOG_FILE` | `/config/webui.log` | 日志文件；留空则只输出到 stdout |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_MAX_MB` | `10` | 单个日志文件上限（MB） |
| `LOG_BACKUPS` | `3` | 轮转保留数量 |
| `MAX_TASKS` | `50` | 内存中保留的任务上限；启动恢复时按此裁剪 |
| `PORT` | `8080` | 容器内监听端口；非法值回退 `8080` |


健康检查
--------

```bash
docker inspect --format '{{.State.Health.Status}}' yt-dlp-webui
```

`HEALTHCHECK` 通过 Python 调用 `app.serve._port()`，再请求 `http://127.0.0.1:<port>/api/formats`。
这样 `PORT` 合法时探针跟随实际端口，非法时与应用一起回退 `8080`。

