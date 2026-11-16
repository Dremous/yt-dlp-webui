#!/bin/sh
# 容器默认以 root 启动，仅用于修正 bind mount 的归属，然后立刻降到 app 用户。
# 这样首次创建 ./downloads、./config 时不会因为 root 拥有目录而导致下载失败。
set -eu

if [ "$(id -u)" = "0" ]; then
    if ! chown -R app:app /downloads /config 2>/dev/null; then
        echo "[entrypoint] warning: 无法修改 /downloads 或 /config 的归属，请检查挂载目录权限" >&2
    fi
    exec gosu app "$@"
fi

exec "$@"
