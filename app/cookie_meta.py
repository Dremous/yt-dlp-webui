"""Cookie 文件元信息：解析 Netscape cookies.txt 的条数与过期状态。

这里只读取 domain、expires、name 等元数据，绝不把 Cookie 值放进返回值或日志。
YouTube 的登录态可能在到期时间之前就被服务端吊销，因此这些字段只能用于提醒，
不能当作“Cookie 一定有效”的证明。
"""

from __future__ import annotations

import time
from typing import Optional

# 距离到期不足 7 天时提前提醒，给用户留出重新导出的时间。
COOKIE_EXPIRY_WARNING_DAYS = 7
_COOKIE_WARNING_SECONDS = COOKIE_EXPIRY_WARNING_DAYS * 24 * 60 * 60

# 登录态相关的常见 Cookie。只用于告诉用户“文件里有没有登录 Cookie”，
# 不参与实际下载，也不验证账号是否仍然有效。
_AUTH_COOKIE_NAMES = frozenset({
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-1PSIDTS",
    "__Secure-3PSID",
    "__Secure-3PSIDTS",
    "LOGIN_INFO",
})


def _parse_expiry(raw: str) -> Optional[int]:
    """把 Netscape 的 expires 字段转成秒级时间戳；0/空值表示会话 Cookie。"""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        expiry = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return expiry if expiry > 0 else None


def parse_cookie_metadata(text: str, *, now: Optional[float] = None) -> dict:
    """统计 Cookie 数量、登录 Cookie 数量和最近的过期时间。

    Netscape 文件每行 7 个制表符字段；`#HttpOnly_` 前缀是数据行而不是注释，
    必须先剥掉前缀再判断。无法解析的行直接跳过，不影响其余 Cookie 的状态展示。
    """
    current = time.time() if now is None else float(now)
    cookie_count = 0
    auth_cookie_count = 0
    session_cookie_count = 0
    expired_count = 0
    future_expiries = []

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.lstrip().startswith("#"):
            continue

        fields = line.split("\t")
        if len(fields) != 7:
            continue

        cookie_count += 1
        name = fields[5].strip()
        if name in _AUTH_COOKIE_NAMES:
            auth_cookie_count += 1

        expiry = _parse_expiry(fields[4])
        if expiry is None:
            session_cookie_count += 1
        elif expiry <= current:
            expired_count += 1
        else:
            future_expiries.append(expiry)

    nearest_expiry = min(future_expiries) if future_expiries else None
    if cookie_count == 0:
        expiry_state = "unknown"
    elif expired_count:
        expiry_state = "expired"
    elif nearest_expiry is not None and nearest_expiry <= current + _COOKIE_WARNING_SECONDS:
        expiry_state = "soon"
    elif nearest_expiry is None:
        expiry_state = "session"
    else:
        expiry_state = "ok"

    return {
        "cookie_count": cookie_count,
        "auth_cookie_count": auth_cookie_count,
        "session_cookie_count": session_cookie_count,
        "expired_count": expired_count,
        "expiring_soon": expiry_state == "soon",
        "nearest_expiry": nearest_expiry,
        "expiry_state": expiry_state,
        "expiry_warning_days": COOKIE_EXPIRY_WARNING_DAYS,
    }
