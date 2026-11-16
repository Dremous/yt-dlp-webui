#!/usr/bin/env python3
"""解析 BtbN latest release 中最高版本的 nX.Y GPL 资产并输出 build-arg。"""

import json
import os
import re
import sys
from typing import Optional
from urllib.request import Request, urlopen

API_URL = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
STABLE_ASSET = re.compile(
    r"^ffmpeg-n([0-9]+\.[0-9]+)-latest-linux64-gpl-\1\.tar\.xz$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ResolutionError(RuntimeError):
    """无法安全解析 BtbN 资产时抛出。"""


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def resolve_release(
    token: Optional[str] = None, api_url: str = API_URL
) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "yt-dlp-webui-build",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(api_url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except Exception as exc:
        raise ResolutionError(
            "GitHub API 不可用，无法解析 BtbN latest release；拒绝发布旧版本"
        ) from exc

    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        raise ResolutionError(
            "BtbN latest release 中没有 nX.Y 稳定 GPL 资产；拒绝发布旧版本"
        )

    versions = set()
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if not isinstance(name, str):
            continue
        match = STABLE_ASSET.match(name)
        if match:
            versions.add(match.group(1))
    if not versions:
        raise ResolutionError(
            "BtbN latest release 中没有 nX.Y 稳定 GPL 资产；拒绝发布旧版本"
        )

    version = max(versions, key=_version_key)
    by_name = {
        asset.get("name"): asset
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    digests = {}
    for arch in ("linux64", "linuxarm64"):
        name = f"ffmpeg-n{version}-latest-{arch}-gpl-{version}.tar.xz"
        asset = by_name.get(name)
        if not asset:
            raise ResolutionError(
                f"BtbN 缺少资产 {name}；拒绝发布不完整的多架构镜像"
            )
        digest = asset.get("digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise ResolutionError(
                f"BtbN 资产缺少 sha256 digest，无法校验下载内容（{name}）"
            )
        digest_value = digest[len("sha256:") :]
        if not SHA256.fullmatch(digest_value):
            raise ResolutionError(
                f"BtbN 资产的 sha256 digest 格式非法，无法校验下载内容（{name}）"
            )
        digests[arch] = digest_value

    return {
        "version": version,
        "sha256_amd64": digests["linux64"],
        "sha256_arm64": digests["linuxarm64"],
    }


def main() -> int:
    try:
        result = resolve_release(token=os.environ.get("GH_TOKEN"))
    except ResolutionError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        print("GITHUB_OUTPUT 未设置，无法传递构建参数", file=sys.stderr)
        return 1

    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"version={result['version']}\n")
        handle.write(f"sha256_amd64={result['sha256_amd64']}\n")
        handle.write(f"sha256_arm64={result['sha256_arm64']}\n")
    print(f"Resolved BtbN FFmpeg GPL stable branch: n{result['version']}")
    print(f"sha256 amd64={result['sha256_amd64']}")
    print(f"sha256 arm64={result['sha256_arm64']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
