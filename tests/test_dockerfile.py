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

    def test_compose_bind_addr_can_limit_listening_interface(self):
        content = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        # 默认保留 0.0.0.0 以不破坏现有 LAN 访问；BIND_ADDR 可收紧到具体网卡/IP。
        self.assertIn("BIND_ADDR", content)
        self.assertIn("${BIND_ADDR:-0.0.0.0}", content)

    def test_image_does_not_hardcode_expose_port(self):
        """EXPOSE 是静态元数据，写死 8080 会和运行时 PORT 不一致。"""
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("EXPOSE 8080", content)

    def test_app_version_is_injected_after_dependency_layers(self):
        """版本号每次构建都会变，放在顶部会让 apt/pip/npm 缓存全部失效。"""
        instructions = self._instructions(
            (self.root / "Dockerfile").read_text(encoding="utf-8")
        )
        version_at = self._instruction_index(instructions, "ARG APP_VERSION")
        self.assertGreater(
            version_at,
            self._instruction_index(
                instructions, "RUN pip install --no-cache-dir -r requirements.txt"
            ),
        )
        self.assertGreater(
            version_at, self._instruction_index(instructions, "COPY app ./app")
        )
        self.assertLess(
            version_at, self._instruction_index(instructions, "HEALTHCHECK")
        )

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

    def test_workflow_cache_ref_is_isolated_by_ref(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        # main 与 tag 不能共用同一个 cache tag，否则会互相覆盖。
        self.assertIn("SAFE_REF=", content)
        self.assertIn("REF_HASH=", content)
        self.assertIn("tr -cs", content)
        self.assertIn("sha256sum", content)
        self.assertIn(
            'CACHE_TAG="buildcache-${GITHUB_REF_TYPE}-${SAFE_REF}-${REF_HASH}"',
            content,
        )
        self.assertIn(
            "CACHE_REF=ghcr.io/${GITHUB_REPOSITORY,,}:${CACHE_TAG}", content
        )

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
        self.assertIn(
            "from=type=registry,ref=${{ steps.image.outputs.CACHE_REF }}", content
        )
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
        # ignore-error 会吞掉 cache-to 失败；单独 verify，避免静默丢缓存。
        self.assertIn("name: Verify registry cache", content)
        self.assertIn("docker buildx imagetools inspect", content)

    def test_workflow_does_not_cancel_in_progress_build(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(content, r"(?m)^\s*cancel-in-progress:\s*false\s*$")


if __name__ == "__main__":
    unittest.main()
