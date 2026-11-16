"""镜像与 GitHub Actions 配置回归测试。"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class DockerfileConfigTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def _instructions(self, content):
        """按 Dockerfile 指令行做顺序断言，避免被注释和缩进影响。"""
        return [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    def _instruction_index(self, instructions, prefix):
        for index, line in enumerate(instructions):
            if line.startswith(prefix):
                return index
        self.fail(f"没有找到 Dockerfile 指令：{prefix}")

    def test_healthcheck_uses_the_same_port_fallback_as_the_app(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("from app.serve import _port", content)
        self.assertNotIn("http://127.0.0.1:8080", content)
        self.assertIn("ENV PORT=8080", content)

    def test_compose_port_mapping_follows_port_env(self):
        content = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("${PORT:-8080}:${PORT:-8080}", content)
        self.assertIn("PORT=${PORT:-8080}", content)

    def test_compose_defaults_to_latest_image_tag(self):
        content = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn(
            'ghcr.io/dremous/yt-dlp-webui:${IMAGE_TAG:-latest}', content
        )

    def test_compose_bind_addr_can_limit_listening_interface(self):
        content = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        # 默认保留 0.0.0.0 以不破坏现有 LAN 访问；BIND_ADDR 可收紧到具体网卡/IP。
        self.assertIn("BIND_ADDR", content)
        self.assertIn("${BIND_ADDR:-0.0.0.0}", content)

    def test_image_does_not_hardcode_expose_port(self):
        """EXPOSE 是静态元数据，写死 8080 会和运行时 PORT 不一致。"""
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("EXPOSE 8080", content)

    def test_app_version_is_injected_only_in_final_metadata_stage(self):
        """版本号会变，必须只落在最终元数据阶段，不能污染安装 ffmpeg 的 base 阶段。"""
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        runtime_body = content.split("FROM python:3.13-slim AS runtime", 1)[1].split(
            "FROM runtime AS latest-base", 1
        )[0]
        self.assertNotIn("ARG APP_VERSION", runtime_body)
        self.assertNotIn("ENV APP_VERSION", runtime_body)

        self.assertEqual(content.count("ARG APP_VERSION=dev"), 1)
        latest_base = content.split("FROM runtime AS latest-base", 1)[1].split(
            "FROM latest-base AS latest", 1
        )[0]
        self.assertNotIn("ARG APP_VERSION", latest_base)
        self.assertNotIn("ENV APP_VERSION", latest_base)

        latest_final = content.split("FROM latest-base AS latest", 1)[1]
        self.assertIn("ARG APP_VERSION=dev", latest_final)
        self.assertIn("ENV APP_VERSION=${APP_VERSION}", latest_final)
        # 元数据阶段不能再带 COPY / RUN，否则版本一变又会拖累重活。
        self.assertNotIn("COPY ", latest_final)
        self.assertNotIn("RUN ", latest_final)

    def test_runtime_stage_is_not_duplicated(self):
        instructions = self._instructions(
            (self.root / "Dockerfile").read_text(encoding="utf-8")
        )
        python_stages = [
            line
            for line in instructions
            if line.startswith("FROM ") and "python:3.13-slim" in line
        ]
        self.assertEqual(len(python_stages), 1)

    def test_latest_target_uses_btbn_and_is_default(self):
        """默认 build target 必须是 latest，且 latest 使用 BtbN 静态 ffmpeg。"""
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        instructions = self._instructions(content)
        from_lines = [line for line in instructions if line.startswith("FROM ")]
        self.assertEqual(from_lines[-1], "FROM latest-base AS latest")
        self.assertIn("FROM runtime AS latest-base", from_lines)
        self.assertNotIn("FROM runtime AS slim-base", from_lines)
        self.assertNotIn("FROM slim-base AS slim", from_lines)

        latest_base = content.split("FROM runtime AS latest-base", 1)[1].split(
            "FROM latest-base AS latest", 1
        )[0]
        self.assertIn(
            "COPY --from=ffmpeg-static /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg",
            latest_base,
        )
        self.assertIn(
            "COPY --from=ffmpeg-static /opt/ffmpeg/bin/ffprobe /usr/local/bin/ffprobe",
            latest_base,
        )
        self.assertIn("ffmpeg -version", latest_base)
        self.assertIn("ffprobe -version", latest_base)
        self.assertNotIn("apt-get install -y --no-install-recommends ffmpeg", latest_base)
        latest_final = content.split("FROM latest-base AS latest", 1)[1]
        self.assertNotIn("apt-get", latest_final)

    def test_dockerfile_has_no_apt_ffmpeg_variant(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("apt-get install -y --no-install-recommends ffmpeg", content)
        self.assertNotIn("AS latest-base\nRUN apt-get", content)
        self.assertNotIn("AS slim", content)

    def test_latest_target_uses_latest_stable_btbn_gpl_build(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        # BtbN 的 latest release 里同时有 master 和 n9.0/n8.1 稳定分支；
        # 这里只取稳定分支的 GPL 构建，不能让 latest 变成 master 快照。
        self.assertIn("ARG FFMPEG_BUILDS_VERSION=9.0", content)
        self.assertIn("FROM debian:bookworm-slim AS ffmpeg-static", content)
        self.assertIn("BtbN/FFmpeg-Builds/releases/download/latest", content)
        self.assertIn("linux64", content)
        self.assertIn("linuxarm64", content)
        self.assertIn("-gpl-", content)
        self.assertIn("FFMPEG_BUILDS_REFRESH", content)
        self.assertIn("ffmpeg-n${FFMPEG_BUILDS_VERSION}", content)
        self.assertNotIn("mwader/static-ffmpeg", content)
        self.assertNotIn("FFMPEG_STATIC_IMAGE", content)
        # 不再有写死的 gzip SHA256，也不从 eugeneware 的 Release 下载；
        # 新的校验来自 GitHub Release asset 的 digest（见 checksum 测试）。
        self.assertNotIn("ffmpeg-linux-x64.gz", content)

        latest_base = content.split("FROM runtime AS latest-base", 1)[1].split(
            "FROM latest-base AS latest", 1
        )[0]
        self.assertIn(
            "COPY --from=ffmpeg-static /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg",
            latest_base,
        )
        self.assertIn(
            "COPY --from=ffmpeg-static /opt/ffmpeg/bin/ffprobe /usr/local/bin/ffprobe",
            latest_base,
        )
        self.assertIn("COPY --from=ffmpeg-static /opt/ffmpeg/LICENSE.txt", latest_base)
        self.assertIn("ffmpeg -version", latest_base)
        self.assertIn("ffprobe -version", latest_base)

    def test_dockerfile_verifies_btbn_asset_checksum(self):
        dockerfile = (self.root / "Dockerfile").read_text(encoding="utf-8")
        workflow = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        # GitHub Release asset 自带 sha256 digest，CI 必须把它传给 Dockerfile。
        self.assertIn("ARG FFMPEG_BUILDS_SHA256_AMD64", dockerfile)
        self.assertIn("ARG FFMPEG_BUILDS_SHA256_ARM64", dockerfile)
        self.assertIn("sha256sum -c", dockerfile)
        self.assertIn("FFMPEG_BUILDS_SHA256_AMD64=${{ steps.ffmpeg.outputs.sha256_amd64 }}", workflow)
        self.assertIn("FFMPEG_BUILDS_SHA256_ARM64=${{ steps.ffmpeg.outputs.sha256_arm64 }}", workflow)
        self.assertIn("sha256_amd64", workflow)
        self.assertIn("sha256_arm64", workflow)
        self.assertIn("digest", workflow)

    def test_dockerfile_includes_ffmpeg_source_offer(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        # GPL 二进制需要保留对应源码/构建脚本的提供方式；源码地址必须
        # 由 version.txt 中的 git commit 生成，不能指向不对应的 release 包。
        self.assertIn("SOURCE.txt", content)
        self.assertIn("FFmpeg/FFmpeg/tree/${COMMIT}", content)
        self.assertIn("BtbN/FFmpeg-Builds", content)
        self.assertNotIn("ffmpeg.org/releases", content)

    def test_dockerfile_installs_bgutil_pot_provider_plugin(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        # 插件只负责向 sidecar 取 token；版本与 compose 的 provider 镜像一起锁定，
        # 避免插件和 provider 的协议大版本漂移。
        self.assertIn("bgutil-ytdlp-pot-provider==2.0.2", content)
        self.assertIn("import yt_dlp_plugins.extractor.getpot_bgutil", content)

    def test_compose_runs_pot_sidecar_and_points_webui_at_it(self):
        content = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("bgutil-provider:", content)
        self.assertIn(
            "brainicism/bgutil-ytdlp-pot-provider:${BGUTIL_VERSION:-2.0.2}",
            content,
        )
        self.assertIn("POT_PROVIDER_URL=http://bgutil-provider:4416", content)
        self.assertIn('expose:\n      - "4416"', content)

    def test_workflow_resolves_latest_stable_btbn_gpl_build(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        # 每次构建解析 BtbN latest release 中最高版本的 nX.Y 稳定 GPL 资产，
        # 并通过 build-arg 覆盖 Dockerfile 的本地 fallback。
        self.assertIn("Resolve latest stable BtbN FFmpeg GPL build", content)
        self.assertIn("python3 .github/scripts/resolve_btbn_release.py", content)
        self.assertIn("steps.ffmpeg.outputs.version", content)
        self.assertIn(
            "FFMPEG_BUILDS_VERSION=${{ steps.ffmpeg.outputs.version }}", content
        )
        self.assertIn("FFMPEG_BUILDS_REFRESH=${{ github.run_id }}", content)
        self.assertNotIn("FALLBACK", content)

    def test_workflow_fails_when_btbn_release_resolution_fails(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        resolver = (self.root / ".github/scripts/resolve_btbn_release.py").read_text(
            encoding="utf-8"
        )
        # CI 发布 latest 是浮动标签，解析失败必须失败，不能静默回退旧版本。
        self.assertIn("resolve_btbn_release.py", content)
        self.assertIn("GitHub API 不可用", resolver)
        self.assertIn("BtbN latest release 中没有", resolver)
        self.assertIn("BtbN 缺少资产", resolver)
        self.assertIn("ResolutionError", resolver)
        self.assertNotIn('VERSION="$FALLBACK"', resolver)

    def test_workflow_cache_ref_is_isolated_by_ref(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        # main 与 tag 不能共用同一个 cache tag，否则会互相覆盖；
        # 单一 latest 变体使用固定的 latest 前缀，branch/tag 名清洗后再拼 hash。
        self.assertIn("SAFE_REF=", content)
        self.assertIn("REF_HASH=", content)
        self.assertIn("tr -cs", content)
        self.assertIn("sha256sum", content)
        self.assertIn(
            'CACHE_TAG="buildcache-latest-${GITHUB_REF_TYPE}-${SAFE_REF}-${REF_HASH}"',
            content,
        )
        self.assertIn(
            "CACHE_REF=ghcr.io/${IMAGE_NAME}:${CACHE_TAG}", content
        )

    def test_workflow_builds_single_latest_target(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("target: latest", content)
        self.assertNotIn("variant: slim", content)
        self.assertNotIn("matrix.variant", content)
        self.assertIn("ghcr.io/${IMAGE_NAME}:latest", content)
        self.assertIn("ghcr.io/${IMAGE_NAME}:${DATE}", content)
        self.assertIn("ghcr.io/${IMAGE_NAME}:${DATE}-${SHORT_SHA}", content)
        self.assertNotIn("${DATE}-${VARIANT}", content)
        self.assertNotIn("${DATE}-${SHORT_SHA}-${VARIANT}", content)

    def test_workflow_uses_single_cache_ref(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        # 单一变体只读自己的 CACHE_REF，不再存在另一变体的 cache 复用逻辑。
        self.assertNotIn("OTHER_CACHE_REF", content)
        self.assertNotIn("OTHER_VARIANT", content)
        self.assertIn("steps.image.outputs.CACHE_REF", content)
        self.assertIn("from<<EOF", content)

    def test_workflow_smoke_tests_latest(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("smoke:", content)
        self.assertNotIn("matrix:", content)
        self.assertIn("--entrypoint ffmpeg", content)
        self.assertIn("--entrypoint ffprobe", content)
        self.assertIn("Verify MP3 encoder", content)
        self.assertIn("libmp3lame", content)
        self.assertIn('docker pull --platform "$PLATFORM"', content)
        self.assertIn('docker run --rm --platform "$PLATFORM"', content)
        self.assertIn("IMAGE_DIGEST", content)
        self.assertIn("needs.build.outputs.digest", content)
        self.assertIn("ghcr.io/${IMAGE_NAME}@${IMAGE_DIGEST}", content)
        self.assertNotIn("variant: [latest, slim]", content)

    def test_workflow_latest_tag_only_follows_main(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('if [ "${GITHUB_REF}" = "refs/heads/main" ]', content)
        self.assertIn("tag / 其他 ref 构建不能覆盖它", content)

    def test_workflow_smoke_uses_immutable_build_digest(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("outputs:", content)
        self.assertIn("digest: ${{ steps.build.outputs.digest }}", content)
        self.assertIn("id: build", content)
        smoke = content.split("smoke:", 1)[1]
        self.assertIn("IMAGE_DIGEST", smoke)
        self.assertIn("ghcr.io/${IMAGE_NAME}@${IMAGE_DIGEST}", smoke)
        self.assertNotIn("ghcr.io/${IMAGE_NAME}:latest", smoke)

    def test_workflow_smoke_tests_both_architectures(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        smoke = content.split("smoke:", 1)[1]
        self.assertIn("Set up QEMU", smoke)
        self.assertIn("linux/amd64", smoke)
        self.assertIn("linux/arm64", smoke)

    def test_workflow_smoke_checks_gpl_compliance_files(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        smoke = content.split("smoke:", 1)[1]
        self.assertIn("Verify GPL compliance files", smoke)
        self.assertIn("/usr/share/doc/ffmpeg/LICENSE", smoke)
        self.assertIn("/usr/share/doc/ffmpeg/SOURCE.txt", smoke)
        self.assertIn("/usr/share/doc/ffmpeg/version.txt", smoke)

    def test_workflow_checksum_comment_is_accuracy_limited(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        # GitHub digest 与 asset 同源，只能说明下载内容与 release 元数据一致，
        # 不能承诺防供应链替换。
        self.assertIn("完整性校验", content)
        self.assertNotIn("避免下载到损坏/被替换", content)

    def test_runtime_user_can_be_overridden_with_puid_pgid(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ENV PUID=10001", content)
        self.assertIn("PGID=10001", content)
        self.assertIn("groupadd -r -g 10001 app", content)
        self.assertIn("-u 10001", content)

    def test_runtime_timezone_can_be_overridden(self):
        dockerfile = (self.root / "Dockerfile").read_text(encoding="utf-8")
        compose = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        entrypoint = (self.root / "docker-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("ENV TZ=Asia/Shanghai", dockerfile)
        self.assertIn("tzdata", dockerfile)
        self.assertIn("TZ=${TZ:-Asia/Shanghai}", compose)
        self.assertIn("is_valid_timezone", entrypoint)

    def test_runtime_drops_privileges(self):
        dockerfile = (self.root / "Dockerfile").read_text(encoding="utf-8")
        entrypoint = (self.root / "docker-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("gosu", dockerfile)
        self.assertIn("docker-entrypoint.sh", dockerfile)
        self.assertIn("ENTRYPOINT", dockerfile)
        self.assertIn('exec gosu "$PUID:$PGID"', entrypoint)
        self.assertIn('chown -R "$PUID:$PGID" /downloads /config /home/app', entrypoint)

    def test_entrypoint_applies_puid_pgid_to_mounts_and_process(self):
        """用假 id/chown/gosu 验证 root 分支：按 PUID/PGID 改归属并降权。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            script = root / "docker-entrypoint.sh"
            shutil.copy2(self.root / "docker-entrypoint.sh", script)
            log = root / "calls.log"

            commands = {
                "id": '#!/bin/sh\n'
                      'if [ "$1" = "-u" ]; then echo 0; exit 0; fi\n'
                      'exec /usr/bin/id "$@"\n',
                "chown": '#!/bin/sh\n'
                         'printf "chown %s\\n" "$*" >> "$ENTRYPOINT_LOG"\n',
                "gosu": '#!/bin/sh\n'
                        'printf "gosu %s TZ=%s\\n" "$*" "$TZ" >> "$ENTRYPOINT_LOG"\n',
            }
            for name, body in commands.items():
                path = bin_dir / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
            env["ENTRYPOINT_LOG"] = str(log)
            env["PUID"] = "1000"
            env["PGID"] = "1000"
            env["TZ"] = "Asia/Shanghai"

            result = subprocess.run(
                ["/bin/sh", str(script), "true"],
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("chown -R 1000:1000 /downloads /config /home/app", calls)
            self.assertIn("gosu 1000:1000 true TZ=Asia/Shanghai", calls)

    def test_entrypoint_defaults_timezone_to_shanghai(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            script = root / "docker-entrypoint.sh"
            shutil.copy2(self.root / "docker-entrypoint.sh", script)
            log = root / "calls.log"

            commands = {
                "id": '#!/bin/sh\n'
                      'if [ "$1" = "-u" ]; then echo 0; exit 0; fi\n'
                      'exec /usr/bin/id "$@"\n',
                "chown": '#!/bin/sh\n'
                         'printf "chown %s\\n" "$*" >> "$ENTRYPOINT_LOG"\n',
                "gosu": '#!/bin/sh\n'
                        'printf "gosu %s TZ=%s\\n" "$*" "$TZ" >> "$ENTRYPOINT_LOG"\n',
            }
            for name, body in commands.items():
                path = bin_dir / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
            env["ENTRYPOINT_LOG"] = str(log)
            env["PUID"] = "10001"
            env["PGID"] = "10001"
            env.pop("TZ", None)

            result = subprocess.run(
                ["/bin/sh", str(script), "true"],
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "gosu 10001:10001 true TZ=Asia/Shanghai",
                log.read_text(encoding="utf-8"),
            )

    def test_entrypoint_skips_recursive_chown_when_owner_marker_matches(self):
        """PUID/PGID 未变化时不重复递归 chown 大媒体库。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            script = root / "docker-entrypoint.sh"
            shutil.copy2(self.root / "docker-entrypoint.sh", script)
            marker = root / "owner-marker"
            marker.write_text("1000:1000\n", encoding="utf-8")
            log = root / "calls.log"

            commands = {
                "id": '#!/bin/sh\n'
                      'if [ "$1" = "-u" ]; then echo 0; exit 0; fi\n'
                      'exec /usr/bin/id "$@"\n',
                "chown": '#!/bin/sh\n'
                         'printf "chown %s\\n" "$*" >> "$ENTRYPOINT_LOG"\n',
                "gosu": '#!/bin/sh\n'
                        'printf "gosu %s\\n" "$*" >> "$ENTRYPOINT_LOG"\n',
            }
            for name, body in commands.items():
                path = bin_dir / name
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
            env["ENTRYPOINT_LOG"] = str(log)
            env["OWNER_MARKER"] = str(marker)
            env["PUID"] = "1000"
            env["PGID"] = "1000"

            result = subprocess.run(
                ["/bin/sh", str(script), "true"],
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("chown ", log.read_text(encoding="utf-8"))
            self.assertIn("gosu 1000:1000 true", log.read_text(encoding="utf-8"))

    def test_entrypoint_rejects_non_numeric_puid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            script = root / "docker-entrypoint.sh"
            shutil.copy2(self.root / "docker-entrypoint.sh", script)
            log = root / "calls.log"

            (bin_dir / "id").write_text(
                '#!/bin/sh\necho 0\n', encoding="utf-8"
            )
            (bin_dir / "id").chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
            env["ENTRYPOINT_LOG"] = str(log)
            env["PUID"] = "abc"
            env["PGID"] = "1000"

            result = subprocess.run(
                ["/bin/sh", str(script), "true"],
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PUID", result.stderr)
            self.assertFalse(log.exists(), "非法 UID 不应继续 chown/gosu")

    def test_entrypoint_rejects_invalid_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            script = root / "docker-entrypoint.sh"
            shutil.copy2(self.root / "docker-entrypoint.sh", script)
            log = root / "calls.log"

            (bin_dir / "id").write_text(
                '#!/bin/sh\necho 0\n', encoding="utf-8"
            )
            (bin_dir / "id").chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
            env["ENTRYPOINT_LOG"] = str(log)
            env["TZ"] = "Mars/Olympus"

            result = subprocess.run(
                ["/bin/sh", str(script), "true"],
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TZ", result.stderr)
            self.assertFalse(log.exists(), "非法时区不应继续 chown/gosu")

    def test_workflow_uses_same_ref_for_cache_read_and_write(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        # 首次没有 buildcache 时不传 cache-from，有缓存时用的仍是同一个 CACHE_REF。
        self.assertIn("name: Resolve cache source", content)
        self.assertIn("steps.cache.outputs.from", content)
        self.assertIn('echo "type=registry,ref=${REF}"', content)
        self.assertIn(
            "cache-to: type=registry,ref=${{ steps.image.outputs.CACHE_REF }}",
            content,
        )
        self.assertIn("mode=max", content)
        self.assertIn("ignore-error=true", content)

    def test_workflow_verifies_cache_export_result(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        cache_script = (
            self.root / ".github/scripts/verify_registry_cache.sh"
        ).read_text(encoding="utf-8")
        # ignore-error 会吞掉 cache-to 失败；单独 verify，避免静默丢缓存。
        self.assertIn("name: Verify registry cache", content)
        self.assertIn("verify_registry_cache.sh", content)
        self.assertIn("docker buildx imagetools inspect", cache_script)

    def test_workflow_cache_verification_is_non_blocking(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        cache_script = (
            self.root / ".github/scripts/verify_registry_cache.sh"
        ).read_text(encoding="utf-8")
        # 镜像已推送后，缓存导出失败只应告警，不应把发布 job 变红并跳过 smoke。
        self.assertIn("verify_registry_cache.sh", content)
        self.assertIn("::warning::registry cache", cache_script)
        self.assertIn("if docker buildx imagetools inspect", cache_script)

    def test_workflow_does_not_cancel_in_progress_build(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(content, r"(?m)^\s*cancel-in-progress:\s*false\s*$")

    def test_docs_describe_single_latest_image(self):
        quickstart = (self.root / "docs/quickstart.md").read_text(encoding="utf-8")
        operations = (self.root / "docs/operations.md").read_text(encoding="utf-8")
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("BtbN/FFmpeg-Builds", operations)
        self.assertIn("latest", operations)
        self.assertNotIn("IMAGE_TAG=slim", quickstart)
        self.assertNotIn(":slim", quickstart)
        self.assertNotIn(":slim", readme)
        self.assertNotIn("slim（静态 ffmpeg）", operations)
        self.assertNotIn("abc1234", operations)
        self.assertNotIn("abc1234", quickstart)
        self.assertNotIn("abc1234", readme)
        # 日期标签是构建示例，不应把某一次的短 SHA 写死成唯一合法值。
        self.assertRegex(quickstart, r"20\d{6}-[0-9a-f]{7,40}")
        self.assertIn("日期标签用于定位", quickstart)
        self.assertIn("digest", quickstart)

    def test_docs_describe_current_ffmpeg_version_dynamically(self):
        operations = (self.root / "docs/operations.md").read_text(encoding="utf-8")
        # 不在文档里写死某次 BtbN 的 build number，避免上游更新后过期。
        self.assertNotIn("n9.0.2-57-g", operations)
        self.assertIn("version.txt", operations)

    def test_docs_describe_cache_invalidation(self):
        operations = (self.root / "docs/operations.md").read_text(encoding="utf-8")
        # FFMPEG_BUILDS_REFRESH 让 ffmpeg-static 每次变化，依赖它的 latest-base 也会重建。
        self.assertIn("可复用的是 `runtime`", operations)
        self.assertIn("`latest-base` 会随", operations)
        self.assertIn("复制静态 ffmpeg", operations)


if __name__ == "__main__":
    unittest.main()
