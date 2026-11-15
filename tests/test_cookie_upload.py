"""Cookie 上传与 app.main 路由层测试。"""

import asyncio
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# app.main 顶层会 import yt_dlp；本地没装时用最小 stub 顶替
if "yt_dlp" not in sys.modules:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = object
    sys.modules["yt_dlp"] = _stub

# app.main 的 /api/about 会读 yt_dlp.version；其他测试可能先装了一个只有
# YoutubeDL 的 stub，这里补齐版本信息，保证路由测试不依赖用例顺序。
_ytdlp = sys.modules["yt_dlp"]
if not hasattr(_ytdlp, "version"):
    _ytdlp.version = types.SimpleNamespace(__version__="test")

# app.main 在 import 时就会建下载目录、初始化任务库；测试先指到临时目录
_TMP_DIR = tempfile.TemporaryDirectory(prefix="cookie-upload-test-")
unittest.addModuleCleanup(_TMP_DIR.cleanup)
_TMP = Path(_TMP_DIR.name)
os.environ["DOWNLOAD_DIR"] = str(_TMP / "downloads")
os.environ["COOKIE_FILE"] = str(_TMP / "config" / "cookies.txt")
os.environ["PROXY_FILE"] = str(_TMP / "config" / "proxy.txt")
os.environ["TASKS_DB"] = str(_TMP / "config" / "tasks.db")
os.environ["LOG_FILE"] = "none"

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import main
from app.accesslog import QuietPollingFilter

LIMIT = main.MAX_COOKIE_BYTES

# app.main 在 import 时会给 uvicorn.access 挂轮询静默过滤器，它会把别的测试里
# 手工打的 GET /api/tasks 日志也吞掉。本文件测完就摘掉，避免跨模块的测试顺序耦合。
logging.getLogger("uvicorn.access").filters = [
    f for f in logging.getLogger("uvicorn.access").filters
    if not isinstance(f, QuietPollingFilter)
]


class _FakeUpload:
    """模拟 Starlette UploadFile 的 size / 分块读取行为。"""

    def __init__(self, size, data=b"", refuse_read=False):
        self.size = size
        self._data = data
        self.read_calls = []
        self.bytes_read = 0
        self._refuse_read = refuse_read

    async def read(self, n=-1):
        self.read_calls.append(n)
        if self._refuse_read:
            raise AssertionError("超限文件不应该被读取")
        if n is None or n < 0:
            chunk, self._data = self._data, b""
        else:
            chunk, self._data = self._data[:n], self._data[n:]
        self.bytes_read += len(chunk)
        return chunk


class CookieUploadTest(unittest.TestCase):
    def test_oversized_upload_is_rejected_without_reading(self):
        fake = _FakeUpload(LIMIT + 1, refuse_read=True)

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(main._read_cookie_upload(fake))

        self.assertEqual(ctx.exception.status_code, 413)
        self.assertEqual(fake.read_calls, [], "size 已知超限时不应先读进内存")

    def test_unknown_size_reads_are_capped(self):
        fake = _FakeUpload(None, b"x" * (LIMIT + 1024))

        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(main._read_cookie_upload(fake))

        self.assertEqual(ctx.exception.status_code, 413)
        self.assertLessEqual(fake.bytes_read, LIMIT + 1, "最多只应多读 1 字节用于判超限")

    def test_route_rejects_oversized_cookie_before_reading(self):
        client = TestClient(main.app)
        with mock.patch.object(
            main.UploadFile,
            "read",
            new=mock.AsyncMock(side_effect=AssertionError("不应调用 read")),
        ):
            response = client.post(
                "/api/cookies",
                files={"file": ("cookies.txt", b"x" * (LIMIT + 1), "text/plain")},
            )
        self.assertEqual(response.status_code, 413)
        self.assertIn("5MB", response.json()["detail"])


class MainRoutesTest(unittest.TestCase):
    """app.main 路由层冒烟测试：覆盖返回结构、参数校验与文件列表边界。"""

    @classmethod
    def setUpClass(cls):
        # 500 也当普通响应返回，symlink 根目录这类回归才能断言状态码而不是抛异常
        cls.client = TestClient(main.app, raise_server_exceptions=False)

    def _set_download_dir(self, root):
        old = main.DOWNLOAD_DIR
        main.DOWNLOAD_DIR = Path(root)
        self.addCleanup(setattr, main, "DOWNLOAD_DIR", old)

    def test_formats_route(self):
        response = self.client.get("/api/formats")
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()}
        self.assertIn("best", ids)
        self.assertIn("mp3", ids)

    def test_about_route(self):
        response = self.client.get("/api/about")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["name"], "yt-dlp WebUI")
        self.assertEqual(data["author"], "Dremous")
        self.assertIn("version", data)

    def test_tasks_route_shape(self):
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {"total", "ended", "tasks"})
        self.assertIsInstance(data["tasks"], list)
        self.assertGreaterEqual(data["total"], data["ended"])

    def test_download_route_rejects_invalid_input(self):
        cases = [
            {"url": ""},
            {"url": "ftp://example.com/v"},
            {"url": "https://example.com/v", "format": "not-a-format"},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post("/api/download", json=payload)
                self.assertEqual(response.status_code, 400)

    def test_unknown_task_routes_return_404(self):
        self.assertEqual(self.client.delete("/api/tasks/nope").status_code, 404)
        self.assertEqual(self.client.post("/api/tasks/nope/cancel").status_code, 404)

    def test_cancel_queued_task_route(self):
        """排队任务必须能从 API 取消；状态迁移由 manager 在锁内完成。"""
        from app.downloader import DownloadTask

        task = DownloadTask("cancel-route", "https://example.com/v", "best")
        with main.manager._lock:
            main.manager.tasks[task.id] = task
        try:
            response = self.client.post(f"/api/tasks/{task.id}/cancel")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(task.status, "cancelled")
        finally:
            with main.manager._lock:
                main.manager.tasks.pop(task.id, None)
            if main.manager._store is not None:
                main.manager._store.delete(task.id)

    def test_cookie_and_proxy_status_routes(self):
        cookie = self.client.get("/api/cookies/status")
        proxy = self.client.get("/api/proxy")
        self.assertEqual(cookie.status_code, 200)
        self.assertEqual(proxy.status_code, 200)
        self.assertFalse(cookie.json()["active"])
        self.assertFalse(proxy.json()["active"])

    def test_files_route_accepts_symlinked_download_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            (real / "sub").mkdir(parents=True)
            (real / "sub" / "a.mp4").write_bytes(b"x")
            link = Path(tmp) / "link"
            link.symlink_to(real, target_is_directory=True)

            self._set_download_dir(link)
            response = self.client.get("/api/files")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.json()], ["sub/a.mp4"])

    def test_files_route_ignores_part_fragments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "keep.mp4").write_bytes(b"v")
            (root / "keep.mp4.part-Frag12").write_bytes(b"f")
            (root / "keep.mp4.part").write_bytes(b"p")

            self._set_download_dir(root)
            response = self.client.get("/api/files")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.json()], ["keep.mp4"])


if __name__ == "__main__":
    unittest.main()
