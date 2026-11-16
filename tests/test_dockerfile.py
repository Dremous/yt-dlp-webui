"""镜像与 GitHub Actions 配置回归测试。"""

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
        self.assertIn("buildcache-${GITHUB_REF_TYPE}-${GITHUB_REF_NAME", content)
        self.assertIn(
            "CACHE_REF=ghcr.io/${GITHUB_REPOSITORY,,}:${CACHE_TAG}", content
        )

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
