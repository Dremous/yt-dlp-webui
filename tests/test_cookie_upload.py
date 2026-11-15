"""Cookie 上传：先看大小再读，避免 5MB 上限被绕过。"""

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


if __name__ == "__main__":
    unittest.main()
