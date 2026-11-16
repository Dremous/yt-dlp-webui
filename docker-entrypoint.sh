#!/bin/sh
# 容器默认以 root 启动，仅用于修正 bind mount 的归属，然后立刻降到非 root 用户。
# PUID/PGID 可在运行时覆盖（如 PUID=1000 PGID=1000），让容器内进程与宿主机
# 用户保持一致，下载文件不会出现「宿主机用户改不动」的 root/10001 归属。
# TZ 控制容器内日志和备份文件名的本地时间，默认 Asia/Shanghai。
set -eu

PUID="${PUID:-10001}"
PGID="${PGID:-10001}"
TZ="${TZ:-Asia/Shanghai}"
# 记录上一次递归修正归属的 UID/GID；默认路径可被测试或特殊部署覆盖。
OWNER_MARKER="${OWNER_MARKER:-/config/.owner-uid-gid}"
# 默认值由本脚本赋值时不会自动进入环境，必须显式导出给 gosu 后的进程。
export TZ

is_valid_id() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$1" -ge 1 ] && [ "$1" -le 2147483647 ]
}

is_valid_timezone() {
    # 只接受 IANA 名称（如 Asia/Shanghai），拒绝绝对路径和 ..，避免 TZ 被当成路径使用。
    case "$1" in
        ''|/*|*..*|*[!A-Za-z0-9_+/-]*) return 1 ;;
    esac
    [ -f "/usr/share/zoneinfo/$1" ]
}

if ! is_valid_timezone "$TZ"; then
    echo "[entrypoint] error: TZ 必须是有效的 IANA 时区（如 Asia/Shanghai），当前 TZ=${TZ}" >&2
    exit 1
fi

if [ "$(id -u)" = "0" ]; then
    if ! is_valid_id "$PUID" || ! is_valid_id "$PGID"; then
        echo "[entrypoint] error: PUID/PGID 必须是 1-2147483647 的整数（当前 PUID=${PUID} PGID=${PGID}）" >&2
        exit 1
    fi

    # HOME 也要跟着改：Deno / yt-dlp 会在 $HOME 下写缓存。
    # 大媒体库上递归 chown 可能很慢；只有首次启动或 PUID/PGID 变化时才全量修正。
    previous_owner="$(cat "$OWNER_MARKER" 2>/dev/null || true)"
    if [ "$previous_owner" = "$PUID:$PGID" ]; then
        echo "[entrypoint] ownership already matches ${PUID}:${PGID}; skip recursive chown"
    else
        if ! chown -R "$PUID:$PGID" /downloads /config /home/app 2>/dev/null; then
            echo "[entrypoint] warning: 无法修改 /downloads、/config 或 /home/app 的归属，请检查挂载目录权限" >&2
        else
            # 标记写失败不影响启动；下次启动会再试一次。
            printf '%s\n' "$PUID:$PGID" > "$OWNER_MARKER" 2>/dev/null || true
        fi
    fi
    exec gosu "$PUID:$PGID" "$@"
fi

exec "$@"
