#!/usr/bin/env bash
# 镜像推送成功后的 registry cache 可观测性检查。
# 缓存导出失败只告警，不改变发布 job 的退出码，避免跳过 smoke。
set -u

REF="${1:-}"
if [ -z "$REF" ]; then
    echo "::error::missing registry cache ref" >&2
    exit 1
fi

if docker buildx imagetools inspect "$REF" >/dev/null 2>&1; then
    echo "Registry cache verified: $REF"
else
    echo "::warning::registry cache 不可读，镜像已推送，仅影响后续构建加速"
fi
