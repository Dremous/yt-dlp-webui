"""日志落盘配置：环境变量控制文件位置、级别与轮转。"""

import logging
import tempfile
import unittest
from pathlib import Path

from app.logconfig import setup_logging


class LogConfigTest(unittest.TestCase):
    def tearDown(self):
        setup_logging({"LOG_FILE": "none", "LOG_LEVEL": "INFO"})  # 清掉测试挂上的 handler

    def test_writes_access_log_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "logs" / "webui.log"
            path = setup_logging({"LOG_FILE": str(log_file), "LOG_CONSOLE": "0"})
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d',
                "192.168.0.2:1", "GET", "/api/tasks?limit=30", "1.1", 200,
            )
            for h in logging.getLogger().handlers:
                h.flush()
            self.assertEqual(path, str(log_file))
            text = log_file.read_text(encoding="utf-8")
            self.assertIn("GET /api/tasks?limit=30", text)
            self.assertIn("uvicorn.access", text)

    def test_disable_file_logging(self):
        self.assertIsNone(setup_logging({"LOG_FILE": "none", "LOG_CONSOLE": "0"}))

    def test_rotation_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "webui.log"
            setup_logging({
                "LOG_FILE": str(log_file), "LOG_MAX_MB": "5", "LOG_BACKUPS": "3",
                "LOG_CONSOLE": "0",
            })
            handlers = [h for h in logging.getLogger().handlers if hasattr(h, "maxBytes")]
            self.assertEqual(len(handlers), 1)
            self.assertEqual(handlers[0].maxBytes, 5 * 1024 * 1024)
            self.assertEqual(handlers[0].backupCount, 3)

    def test_unwritable_path_falls_back_to_console(self):
        # /dev/full 是文件不是目录 → 建父目录会失败，但不能把容器带崩
        self.assertIsNone(setup_logging({"LOG_FILE": "/dev/full/webui.log", "LOG_CONSOLE": "1"}))
        names = [type(h).__name__ for h in logging.getLogger().handlers]
        self.assertIn("StreamHandler", names)

    def test_unwritable_file_keeps_some_output_when_console_off(self):
        # 关了控制台又写不了文件时，至少不能变成「一条日志都没有」
        setup_logging({"LOG_FILE": "/dev/full/webui.log", "LOG_CONSOLE": "0"})
        handlers = logging.getLogger().handlers
        self.assertTrue(handlers, "没有任何日志出口，等于静默吞掉全部日志")

    def test_repeated_setup_does_not_duplicate_handlers(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = str(Path(tmp) / "webui.log")
            env = {"LOG_FILE": log_file, "LOG_CONSOLE": "0"}
            setup_logging(env)
            setup_logging(env)
            self.assertEqual(len(logging.getLogger().handlers), 1)


if __name__ == "__main__":
    unittest.main()
