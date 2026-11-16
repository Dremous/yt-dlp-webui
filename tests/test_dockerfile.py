"""Dockerfile 静态约束测试。

这些用例不执行 docker build，只锁定构建文件里必须保持的关键约束；
真正的镜像构建与运行验证需要在有 Docker 的环境执行。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_dockerfile_uses_slim_base_and_non_root_user():
    text = read("Dockerfile")
    assert "FROM python:3.12-slim-bookworm" in text
    assert "USER app" in text


def test_dockerfile_installs_ffmpeg_and_requirements():
    text = read("Dockerfile")
    assert "ffmpeg" in text
    assert "requirements.txt" in text


def test_dockerfile_builds_frontend_in_separate_stage():
    text = read("Dockerfile")
    assert "AS web-builder" in text
    assert "npm run build" in text
    assert "/build/web/dist" in text


def test_healthcheck_uses_the_same_port_fallback_as_the_app():
    """探针必须复用 app.serve._port()，不能自己拼 ${PORT:-8080}。"""
    text = read("Dockerfile")
    assert "from app.serve import _port" in text
    assert "${PORT:-8080}" not in text
    assert "http.client" in text


def test_compose_port_is_documented():
    text = read("docker-compose.yml")
    assert "${PORT:-8080}:${PORT:-8080}" in text
    assert "1-65535" in text

