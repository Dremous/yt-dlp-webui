"""镜像配置：健康检查必须跟随容器实际监听端口。"""

import unittest
from pathlib import Path


class DockerfilePortTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

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
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        version_at = content.index("ARG APP_VERSION=dev")
        self.assertGreater(
            version_at,
            content.index("RUN pip install --no-cache-dir -r requirements.txt"),
        )
        self.assertGreater(version_at, content.index("COPY app ./app"))
        self.assertLess(version_at, content.index("HEALTHCHECK"))

    def test_runtime_stage_is_not_duplicated(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(content.count("FROM python:3.13-slim"), 1)

    def test_workflow_uses_stable_gha_cache_scope(self):
        content = (self.root / ".github/workflows/build-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("cache-from: type=gha,scope=yt-dlp-webui", content)
        self.assertIn(
            "cache-to: type=gha,mode=max,scope=yt-dlp-webui,ignore-error=true",
            content,
        )


if __name__ == "__main__":
    unittest.main()
