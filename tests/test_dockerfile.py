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
        self.assertIn('ghcr.io/dremous/yt-dlp-webui:${IMAGE_TAG:-latest}', content)

    def test_compose_bind_addr_can_limit_listening_interface(self):
        content = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("BIND_ADDR", content)
        self.assertIn("${BIND_ADDR:-0.0.0.0}", content)

    def test_image_does_not_hardcode_expose_port(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("EXPOSE 8080", content)

    def test_app_version_is_injected_only_in_final_metadata_stages(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        runtime_body = content.split("FROM python:3.13-slim AS runtime", 1)[1].split(
            "FROM runtime AS slim-base", 1
        )[0]
        self.assertNotIn("ARG APP_VERSION", runtime_body)
        self.assertNotIn("ENV APP_VERSION", runtime_body)
        self.assertEqual(content.count("ARG APP_VERSION=dev"), 2)
        slim_base = content.split("FROM runtime AS slim-base", 1)[1].split(
            "FROM slim-base AS slim", 1
        )[0]
        latest_base = content.split("FROM runtime AS latest-base", 1)[1].split(
            "FROM latest-base AS latest", 1
        )[0]
        for base in (slim_base, latest_base):
            self.assertNotIn("ARG APP_VERSION", base)
            self.assertNotIn("ENV APP_VERSION", base)
        slim_final = content.split("FROM slim-base AS slim", 1)[1].split(
            "FROM runtime AS latest-base", 1
        )[0]
        latest_final = content.split("FROM latest-base AS latest", 1)[1]
        for final in (slim_final, latest_final):
            self.assertIn("ARG APP_VERSION=dev", final)
            self.assertIn("ENV APP_VERSION=${APP_VERSION}", final)
            self.assertNotIn("COPY ", final)
            self.assertNotIn("RUN ", final)

    def test_runtime_stage_is_not_duplicated(self):
        instructions = self._instructions(
            (self.root / "Dockerfile").read_text(encoding="utf-8")
        )
        python_stages = [
            line for line in instructions
            if line.startswith("FROM ") and "python:3.13-slim" in line
        ]
        self.assertEqual(len(python_stages), 1)

    def test_latest_target_keeps_apt_ffmpeg_and_is_default(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        instructions = self._instructions(content)
        from_lines = [line for line in instructions if line.startswith("FROM ")]
        self.assertEqual(from_lines[-1], "FROM latest-base AS latest")
        self.assertIn("FROM runtime AS latest-base", from_lines)
        self.assertIn("FROM slim-base AS slim", from_lines)
        latest_base = content.split("FROM runtime AS latest-base", 1)[1].split(
            "FROM latest-base AS latest", 1
        )[0]
        self.assertRegex(latest_base, r"apt-get install[^\n]*\n?\s*ffmpeg")
        self.assertNotIn("ffmpeg-static", latest_base)
        latest_final = content.split("FROM latest-base AS latest", 1)[1]
        self.assertNotIn("apt-get", latest_final)

    def test_slim_target_uses_latest_external_static_ffmpeg_image(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG FFMPEG_STATIC_IMAGE=mwader/static-ffmpeg:9.0.2-1", content)
        self.assertIn("FROM ${FFMPEG_STATIC_IMAGE} AS ffmpeg-static", content)
        self.assertNotIn("ffmpeg-linux-x64.gz", content)
        self.assertNotIn("sha256sum -c", content)
        slim_base = content.split("FROM runtime AS slim-base", 1)[1].split(
            "FROM slim-base AS slim", 1
        )[0]
        self.assertIn("COPY --from=ffmpeg-static /ffmpeg /usr/local/bin/ffmpeg", slim_base)
        self.assertIn("COPY --from=ffmpeg-static /ffprobe /usr/local/bin/ffprobe", slim_base)
        self.assertIn("/versions.json", slim_base)
        self.assertIn("/usr/share/common-licenses/GPL-3", slim_base)
        self.assertIn("ffmpeg -version", slim_base)
        self.assertIn("ffprobe -version", slim_base)

    def test_workflow_resolves_latest_static_ffmpeg_image(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Resolve latest static ffmpeg image", content)
        self.assertIn("mwader/static-ffmpeg", content)
        self.assertIn("steps.ffmpeg.outputs.image", content)
        self.assertIn("FFMPEG_STATIC_IMAGE=${{ steps.ffmpeg.outputs.image }}", content)
        self.assertIn("sort -V", content)
        self.assertIn("9.0.2-1", content)

    def test_workflow_cache_ref_is_isolated_by_ref(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("SAFE_REF=", content)
        self.assertIn("REF_HASH=", content)
        self.assertIn("tr -cs", content)
        self.assertIn("sha256sum", content)
        self.assertIn(
            'CACHE_TAG="buildcache-${VARIANT}-${GITHUB_REF_TYPE}-${SAFE_REF}-${REF_HASH}"',
            content,
        )
        self.assertIn("CACHE_REF=ghcr.io/${IMAGE_NAME}:${CACHE_TAG}", content)

    def test_workflow_builds_latest_and_slim_targets(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("variant: latest", content)
        self.assertIn("variant: slim", content)
        self.assertIn("target: ${{ matrix.target }}", content)
        self.assertIn('if [ "$VARIANT" = "latest" ]', content)
        self.assertIn("ghcr.io/${IMAGE_NAME}:${VARIANT}", content)
        self.assertIn("ghcr.io/${IMAGE_NAME}:${DATE}-${VARIANT}", content)
        self.assertIn(
            "ghcr.io/${IMAGE_NAME}:${DATE}-${SHORT_SHA}-${VARIANT}", content
        )

    def test_workflow_reuses_other_variant_cache_read_only(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("OTHER_CACHE_REF", content)
        self.assertIn('for ref in "${{ steps.image.outputs.CACHE_REF }}"', content)
        self.assertIn("from<<EOF", content)

    def test_workflow_smoke_tests_both_variants(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("smoke:", content)
        self.assertIn("--entrypoint ffmpeg", content)
        self.assertIn("--entrypoint ffprobe", content)
        self.assertIn("Verify slim MP3 encoder", content)
        self.assertIn("libmp3lame", content)

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
                ["/bin/sh", str(script), "true"], env=env, capture_output=True, text=True
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
                ["/bin/sh", str(script), "true"], env=env, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("gosu 10001:10001 true TZ=Asia/Shanghai", log.read_text(encoding="utf-8"))

    def test_entrypoint_rejects_non_numeric_puid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            script = root / "docker-entrypoint.sh"
            shutil.copy2(self.root / "docker-entrypoint.sh", script)
            log = root / "calls.log"
            (bin_dir / "id").write_text('#!/bin/sh\necho 0\n', encoding="utf-8")
            (bin_dir / "id").chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
            env["ENTRYPOINT_LOG"] = str(log)
            env["PUID"] = "abc"
            env["PGID"] = "1000"
            result = subprocess.run(
                ["/bin/sh", str(script), "true"], env=env, capture_output=True, text=True
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
            (bin_dir / "id").write_text('#!/bin/sh\necho 0\n', encoding="utf-8")
            (bin_dir / "id").chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
            env["ENTRYPOINT_LOG"] = str(log)
            env["TZ"] = "Mars/Olympus"
            result = subprocess.run(
                ["/bin/sh", str(script), "true"], env=env, capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("TZ", result.stderr)
            self.assertFalse(log.exists(), "非法时区不应继续 chown/gosu")

    def test_workflow_uses_same_ref_for_cache_read_and_write(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: Resolve cache source", content)
        self.assertIn("steps.cache.outputs.from", content)
        self.assertIn('echo "type=registry,ref=${ref}"', content)
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
        self.assertIn("name: Verify registry cache", content)
        self.assertIn("docker buildx imagetools inspect", content)

    def test_workflow_does_not_cancel_in_progress_build(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(content, r"(?m)^\s*cancel-in-progress:\s*false\s*$")

    def test_docs_explain_slim_image_tag(self):
        quickstart = (self.root / "docs/quickstart.md").read_text(encoding="utf-8")
        operations = (self.root / "docs/operations.md").read_text(encoding="utf-8")
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        for content in (quickstart, operations, readme):
            self.assertIn("slim", content)
        self.assertIn("IMAGE_TAG=slim", quickstart)
        self.assertIn("static-ffmpeg", operations)


if __name__ == "__main__":
    unittest.main()
