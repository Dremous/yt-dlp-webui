"""镜像配置：健康检查必须跟随容器实际监听端口。"""

import unittest
from pathlib import Path


class DockerfilePortTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_healthcheck_uses_port_env(self):
        content = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("${PORT:-8080}", content)
        self.assertNotIn("http://127.0.0.1:8080", content)
        self.assertIn("ENV PORT=8080", content)

    def test_compose_port_mapping_follows_port_env(self):
        content = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("${PORT:-8080}:${PORT:-8080}", content)
        self.assertIn("PORT=${PORT:-8080}", content)


if __name__ == "__main__":
    unittest.main()
