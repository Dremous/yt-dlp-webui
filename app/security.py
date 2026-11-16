"""局域网访问边界：Host 白名单 + 写请求自定义头校验。

服务默认没有账号体系，适合可信的个人局域网。这里加两道低成本防护：

1. Host 只允许 localhost、容器内常见主机名、私有/回环 IP，以及
   WEBUI_ALLOWED_HOSTS 显式列出的域名。这样可以挡住 DNS rebinding：
   攻击者把恶意域名解析到局域网 IP 时，Host 仍是攻击者域名，请求会被拒绝。
2. POST/PUT/PATCH/DELETE 必须带 X-WebUI-Request: 1。浏览器跨站请求设置
   自定义头会触发 CORS 预检，而本服务没有开放 CORS，因此恶意网页无法伪造。

这两道防护不能替代公网场景下的鉴权；公网部署仍应使用反向代理认证或 VPN。
"""

import ipaddress
import os
from typing import Iterable, Optional

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

WEBUI_REQUEST_HEADER = "x-webui-request"
WEBUI_REQUEST_VALUE = "1"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

DEFAULT_ALLOWED_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "host.docker.internal",
})


def _host_from_header(host_header: Optional[str]) -> str:
    """从 Host 头里取出主机名，兼容 domain:port 与 [IPv6]:port。"""
    value = (host_header or "").strip().lower()
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 1 else ""
    if value.count(":") == 1:
        value = value.rsplit(":", 1)[0]
    return value.rstrip(".")


def _env_allowed_hosts() -> set[str]:
    """读取 WEBUI_ALLOWED_HOSTS，逗号分隔；允许用户写域名:端口。"""
    raw = os.environ.get("WEBUI_ALLOWED_HOSTS", "")
    allowed = set()
    for item in raw.split(","):
        host = _host_from_header(item.strip())
        if host:
            allowed.add(host)
    return allowed


def host_allowed(host_header: Optional[str], extra: Optional[Iterable[str]] = None) -> bool:
    """Host 是否在可信集合内：显式域名或私有/回环 IP。

    私有 IP 放行是为了个人 LAN 场景（192.168.x.x、10.x.x.x、IPv6 ULA 等）。
    公网 IP 或未知域名必须显式写进 WEBUI_ALLOWED_HOSTS。
    """
    host = _host_from_header(host_header)
    if not host:
        return False
    if host in DEFAULT_ALLOWED_HOSTS:
        return True
    if extra and host in {h.lower().rstrip(".") for h in extra}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


class LanGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, allowed_hosts: Optional[Iterable[str]] = None):
        super().__init__(app)
        self.allowed_hosts = (
            {h.lower().rstrip(".") for h in allowed_hosts}
            if allowed_hosts is not None
            else _env_allowed_hosts()
        )

    async def dispatch(self, request, call_next):
        if not host_allowed(request.headers.get("host"), self.allowed_hosts):
            return JSONResponse(
                status_code=400,
                content={"detail": "Host not allowed"},
            )
        if (
            request.method in MUTATING_METHODS
            and request.headers.get(WEBUI_REQUEST_HEADER) != WEBUI_REQUEST_VALUE
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing X-WebUI-Request header"},
            )
        return await call_next(request)
