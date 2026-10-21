"""app.tasklog 的单元测试。

只依赖 Python 标准库，**不需要安装 yt-dlp**，因此可以在本地直接运行：

    python -m unittest discover -s tests -v

测试重点：
- 代理/URL 凭证必须被脱敏（代理地址里常带 user:password）
- 先脱敏再截断，截断不能造成凭证片段残留
- 环形缓冲与错误摘要把控给前端的单行摘要
"""

import tempfile
import unittest
from pathlib import Path

from app.tasklog import TaskLogger


class RedactionTest(unittest.TestCase):
    def test_redacts_proxy_credentials(self):
        """yt-dlp 的 Proxy map 调试行会原样打印代理地址，必须被抹掉。"""
        logger = TaskLogger()
        logger.debug("[debug] Proxy map: {'all': 'socks5://Clash:secret@192.168.0.30:7893'}")

        line = logger.redacted_lines()[0]

        self.assertNotIn("secret", line)
        self.assertIn("socks5://***@192.168.0.30:7893", line)

    def test_redacts_credentials_in_unrelated_urls(self):
        """不只代理，日志里任何带凭证的 URL 都要脱敏。"""
        logger = TaskLogger()
        logger.info("fetching https://user:pw@example.com/path")

        self.assertEqual(logger.redacted_lines()[0], "fetching https://***@example.com/path")

    def test_keeps_urls_without_credentials(self):
        """没有凭证的普通 URL 不能被误伤。"""
        logger = TaskLogger()
        logger.info("https://www.youtube.com/watch?v=abc")

        self.assertEqual(logger.redacted_lines()[0], "https://www.youtube.com/watch?v=abc")

    def test_redacts_before_truncating(self):
        """超长行必须先脱敏再截断，否则截断会留下密码片段。

        若顺序反了（先截断到 24 字符），脱敏结果为 'prefix://***@host:12'，
        与本断言不符，因此本用例能真正区分两种实现顺序。
        """
        logger = TaskLogger(max_line_len=24)
        logger.debug("prefix://user:pw@host:1234")

        self.assertEqual(logger.redacted_lines()[0], "prefix://***@host:1234")


class BufferTest(unittest.TestCase):
    def test_drops_oldest_lines_beyond_limit(self):
        """超出上限时丢弃最旧的行，保留最近的日志。"""
        logger = TaskLogger(max_lines=3)
        for i in range(5):
            logger.debug(f"line {i}")

        self.assertEqual(logger.redacted_lines(), ["line 2", "line 3", "line 4"])

    def test_truncates_long_lines(self):
        """单行超长（例如带签名的媒体 URL）要截断，避免撑爆内存和日志文件。"""
        logger = TaskLogger(max_line_len=10)
        logger.debug("x" * 50)

        self.assertEqual(logger.redacted_lines()[0], "x" * 10)

    def test_skips_blank_messages(self):
        """yt-dlp 有时会发空白消息，不应占用缓冲行数。"""
        logger = TaskLogger()
        logger.debug("")
        logger.debug("   ")
        logger.debug("ok")

        self.assertEqual(logger.redacted_lines(), ["ok"])


class MultilineMessageTest(unittest.TestCase):
    """yt-dlp 会把 ERROR 行和整段 traceback 塞进同一条 logger 消息（含多个 \\n）。

    如果不拆，缓冲里的"一行"其实是好几行，会同时破坏两件事：
    - error 摘要被 traceback 路径挤占，前端那一行几乎全是噪音
    - LOG_TAIL_LINES 名义 30 行、实际打印上百行
    """

    def test_splits_multiline_message_into_physical_lines(self):
        logger = TaskLogger()
        logger.error(
            "ERROR: [youtube] abc: This video is unavailable\n"
            '  File "/usr/local/lib/python3.13/site-packages/yt_dlp/extractor/common.py", line 764, in extract\n'
            "    ie_result = self._real_extract(url)"
        )

        self.assertEqual(
            logger.redacted_lines(),
            [
                "ERROR: [youtube] abc: This video is unavailable",
                '  File "/usr/local/lib/python3.13/site-packages/yt_dlp/extractor/common.py", line 764, in extract',
                "    ie_result = self._real_extract(url)",
            ],
        )

    def test_multiline_error_summary_uses_first_line(self):
        logger = TaskLogger()
        logger.error(
            "ERROR: [youtube] abc: This video is unavailable\n"
            '  File "/usr/local/lib/python3.13/site-packages/yt_dlp/extractor/common.py", line 764, in extract'
        )

        summary = logger.error_summary()

        self.assertEqual(summary, "ERROR: [youtube] abc: This video is unavailable")
        self.assertNotIn("\n", summary)

    def test_multiline_lines_are_redacted_individually(self):
        """拆分后每一行都要单独脱敏，不能只处理第一行。"""
        logger = TaskLogger()
        logger.info("line one\nhttps://u:p@example.com/x\nline three")

        self.assertEqual(
            logger.redacted_lines(),
            ["line one", "https://***@example.com/x", "line three"],
        )

    def test_skips_blank_lines_inside_multiline_message(self):
        logger = TaskLogger()
        logger.debug("a\n\n  \nb")

        self.assertEqual(logger.redacted_lines(), ["a", "b"])

    def test_tail_line_count_matches_physical_lines(self):
        """缓冲里每个元素都必须是物理行，取末 N 条才是真正的 N 行。"""
        logger = TaskLogger(max_lines=10)
        logger.debug("a\nb\nc")

        self.assertEqual(logger.redacted_lines(), ["a", "b", "c"])


class ErrorSummaryTest(unittest.TestCase):
    def test_returns_last_error_line(self):
        """前端只显示单行摘要，应取最后一条 error 级别消息。"""
        logger = TaskLogger()
        logger.debug("[debug] 无关行")
        logger.error("ERROR: unable to download video data: HTTP Error 403")
        logger.error("ERROR: [youtube] abc: Requested format is not available")

        self.assertEqual(
            logger.error_summary(),
            "ERROR: [youtube] abc: Requested format is not available",
        )

    def test_falls_back_to_last_line(self):
        """没有 error 级别消息时，用最后一行兜底。"""
        logger = TaskLogger()
        logger.debug("[debug] first")
        logger.info("last line")

        self.assertEqual(logger.error_summary(), "last line")

    def test_returns_none_when_empty(self):
        self.assertIsNone(TaskLogger().error_summary())


class WriteTest(unittest.TestCase):
    def test_write_persists_redacted_content(self):
        """落盘的日志同样必须是脱敏后的内容。"""
        logger = TaskLogger()
        logger.error("ERROR: sock=socks5://u:p@127.0.0.1:7890 boom")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yt-dlp.log"
            logger.write(path)
            text = path.read_text(encoding="utf-8")

        self.assertIn("ERROR:", text)
        self.assertNotIn("u:p@", text)

    def test_write_creates_parent_directory(self):
        """任务目录被并发清理时也要能写出日志。"""
        logger = TaskLogger()
        logger.info("hello")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "yt-dlp.log"
            logger.write(path)

            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
