"""access log 过滤：静音轮询，但别把错误一起吞掉。"""

import logging
import unittest

from app.accesslog import QuietPollingFilter


def _record(method, path, status):
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d',
        ("192.168.0.2:54655", method, path, "1.1", status), None,
    )


class QuietPollingFilterTest(unittest.TestCase):
    def setUp(self):
        self.filter = QuietPollingFilter()

    def test_quiet_successful_polling(self):
        self.assertFalse(self.filter.filter(_record("GET", "/api/tasks?limit=30", 200)))
        self.assertFalse(self.filter.filter(_record("GET", "/api/files", 200)))
        # Docker HEALTHCHECK 每 30 秒探一次 /api/formats
        self.assertFalse(self.filter.filter(_record("GET", "/api/formats", 200)))

    def test_keep_polling_errors(self):
        self.assertTrue(self.filter.filter(_record("GET", "/api/tasks?limit=30", 500)))
        self.assertTrue(self.filter.filter(_record("GET", "/api/tasks", 404)))
        # 健康检查失败时要能看见（容器被标 unhealthy 的线索）
        self.assertTrue(self.filter.filter(_record("GET", "/api/formats", 500)))

    def test_keep_other_endpoints(self):
        self.assertTrue(self.filter.filter(_record("POST", "/api/download", 200)))
        self.assertTrue(self.filter.filter(_record("GET", "/api/about", 200)))
        self.assertTrue(self.filter.filter(_record("DELETE", "/api/tasks/xx", 200)))

    def test_keep_records_with_other_arg_shapes(self):
        # 非 uvicorn 的日志行（没有这五个参数）不该被误伤
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, "hello", None, None)
        self.assertTrue(self.filter.filter(record))


if __name__ == "__main__":
    unittest.main()
