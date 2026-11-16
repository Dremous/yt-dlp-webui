# 代理与 Cookie

[返回 README](../README.md)

## 国内网络：配置代理

大陆网络无法直连 YouTube（以及 Google 系域名），不带代理时任务会以
`Unable to download webpage` / 连接超时之类的错误失败。在 WebUI 的
**「网络代理」** 卡片里填好地址并点 **「测试连通」**，确认能通即可。

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
- 支持 `http` / `https` / `socks5` / `socks5h`。填 `socks5` 会自动按 `socks5h` 保存和使用——区别只在域名由谁解析：
  `socks5` 在容器里本地解析，常拿到被污染或离代理出口很远的 `googlevideo` 记录，连上去容易被对端直接 RST；
  `socks5h` 把主机名交给代理端，与代理出口位置一致，才是容器场景该用的。

### 排错对照

| 测试结果 | 通常原因 |
|---|---|
| 连接被拒绝 | 地址/端口写错，或代理没在监听该端口 |
| 连接超时 | 代理未开启「允许局域网连接」，或防火墙拦了端口 |
| SOCKS 代理不可用 | `socks5` / `socks5h` 需要 PySocks，官方镜像已内置；自建环境请 `pip install PySocks` |
| 连通正常但下载仍失败 | 目标站点本身需要登录凭证，见下一节的 Cookie；YouTube 高画质还可能需要 PO Token |

## PO Token（YouTube 高画质 / bot 校验）

YouTube 的高码率/video-only 流有时会要求 PO Token；只有 Cookie 不一定够。
`docker-compose.yml` 已内置一个可选的 `bgutil-provider` sidecar：

- 主镜像安装了 `bgutil-ytdlp-pot-provider` 插件；
- `bgutil-provider` 只暴露在 compose 内部网络，不映射宿主机端口；
- WebUI 通过 `POT_PROVIDER_URL=http://bgutil-provider:4416` 把地址交给 yt-dlp。

启动：

```bash
docker compose up -d
```

如果代理装在宿主机，compose 已经给两个容器都加了
`host.docker.internal`；代理地址仍填 `http://host.docker.internal:7890`。
provider 生成 token 时会沿用 WebUI 传给 yt-dlp 的代理，不需要在 provider 容器里
再配置一遍代理。

只使用 `docker run` 的快速启动命令没有 sidecar；这时插件会尝试默认的
`127.0.0.1:4416` 并记录一条 provider 不可用的日志，但不会阻止普通下载。
需要 PO Token 时请用 compose，或自行用同一网络启动
`brainicism/bgutil-ytdlp-pot-provider` 并设置 `POT_PROVIDER_URL`。

## 登录 Cookie

下载需要登录的站点（如 YouTube 会员 / 私享视频）时，先在浏览器安装 Cookie 导出插件
（如 "Get cookies.txt LOCALLY"），导出目标站点的 `cookies.txt`，然后在 WebUI 的
**「登录 Cookie」** 卡片中上传。之后所有下载请求都会自动携带这些 Cookie。
可随时上传覆盖或清除。

支持格式：Netscape `cookies.txt`，或浏览器插件导出的 JSON（上传后会转换成 Netscape
格式保存；yt-dlp 的 `cookiefile` 本身只读取 Netscape）。

### Cookie 会过期

YouTube 的会话 Cookie 会轮换，服务端也可能提前吊销；导出的文件不会自动续期。
WebUI 的状态接口会统计 Netscape 的 `expires` 字段：

- 有已过期 Cookie：状态胶囊变红，提示重新导出并导入；
- 7 天内到期：状态胶囊变黄，显示最近过期日期；
- 只有会话 Cookie（`expires=0`）：提示关闭浏览器或退出登录后可能失效。

这只是文件元数据提示，不能证明 Cookie 当前仍被站点接受。如果刚导入就遇到
`403` 或 `Sign in to confirm you're not a bot`，优先检查代理 IP 和 PO Token。
