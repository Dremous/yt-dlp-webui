# 下载失败排查

[返回 README](../README.md)

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

## 关于 JavaScript 运行时

YouTube 的播放地址带 JS 挑战（`n` 参数签名），yt-dlp 需要外部 JS 运行时才能解开。
镜像已内置 **deno** 与配套的 **yt-dlp-ejs** 脚本包，无需额外配置。

若日志里出现 `JS runtimes: none` 或 `[jsc] JS Challenge Providers: ... (unavailable)`，
说明镜像构建不完整（缺少上面两者），YouTube 提取会退化为「部分格式缺失」。
