# API

[返回 README](../README.md)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | Web 界面 |
| GET | `/api/formats` | 可用输出格式 |
| POST | `/api/download` | 提交下载任务，body: `{"url": "...", "format": "best"}`（`format` 缺省即 `best`）；取值见 `/api/formats` |
| GET | `/api/tasks?limit=N` | 任务列表（含进度、后期处理阶段 `stage` 与全量已结束计数 `ended`；limit 默认 30，上限 50） |
| POST | `/api/tasks/{task_id}/cancel` | 取消排队/下载中的任务（失败/已取消任务不可取消） |
| DELETE | `/api/tasks/{task_id}` | 删除一个已结束的任务记录（失败/取消的任务连带清理任务目录，已完成的保留文件） |
| POST | `/api/tasks/cleanup` | 清理全部已结束的任务记录，返回实际删除数量 |
| GET | `/api/files` | 已下载文件列表 |
| GET | `/files/{filename:path}` | 下载指定文件（相对路径，含任务子目录） |
| DELETE | `/api/files/{filename:path}` | 删除指定文件 |
| POST | `/api/cookies` | 上传 Cookie 文件（multipart，字段 `file`；上限 5MB，超出返回 413） |
| GET | `/api/cookies/status` | 查询 Cookie 状态 |
| DELETE | `/api/cookies` | 清除 Cookie |
| GET | `/api/proxy` | 查询代理状态；`url` 已脱敏（如 `http://***@host:7890`），并返回 `has_credentials` |
| PUT | `/api/proxy` | 保存代理，body: `{"url": "http://host.docker.internal:7890"}`；响应同样只返回脱敏地址 |
| DELETE | `/api/proxy` | 清除代理，恢复直连 |
| POST | `/api/proxy/test` | 测试代理能否连通 YouTube（body 可传 `{"url": "..."}` 测未保存的地址） |
| GET | `/api/about` | 应用信息（应用版本 / ffmpeg 版本 / yt-dlp 版本 / 作者） |

所有 `POST` / `PUT` / `PATCH` / `DELETE` 请求必须带 `X-WebUI-Request: 1`（Web 前端会自动携带）。
这是低成本 CSRF 防护：跨站表单/脚本无法设置该自定义头，浏览器会先发 CORS 预检，而本服务不开放 CORS。
Host 只允许 localhost、私网 IP、`host.docker.internal` 或 `WEBUI_ALLOWED_HOSTS` 中显式列出的域名；
公网部署请额外使用 VPN 或反向代理认证，不能只依赖这两道防护。

> 清理接口用 `POST` 而不是 `DELETE /api/tasks`，是为了避免被误读成「删除全部任务」，包括正在下载的那些。
> 任务记录最多保留 50 条，超出时自动裁剪最旧的记录，但**不会删除磁盘文件**——回收残留要靠上面两个接口。
> `tasks.db` 损坏（断电 / 磁盘写满）时会自动改名为 `tasks.db.corrupt-<时间戳>` 并重建空表，服务照常启动；
> 路径不可写或重建失败时降级为本次运行不持久化，同样不会阻塞启动。
