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
| 连通正常但下载仍失败 | 目标站点本身需要登录凭证，见下一节的 Cookie |

## 登录 Cookie

下载需要登录的站点（如 YouTube 会员 / 私享视频）时，先在浏览器安装 Cookie 导出插件
（如 "Get cookies.txt LOCALLY"），导出目标站点的 `cookies.txt`，然后在 WebUI 的
**「登录 Cookie」** 卡片中上传。之后所有下载请求都会自动携带这些 Cookie。
可随时上传覆盖或清除。

支持格式：Netscape `cookies.txt` 或 JSON（yt-dlp 的 `cookiefile` 选项）。
