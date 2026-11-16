"""进度回调的行为测试。

yt-dlp 把「视频轨 + 音频轨」当两个任务串行处理（YoutubeDL.process_info 外层
`for fmt in formats_to_download`），每换一个流，`total_bytes` 就变成该流自己的大小。
直接拿它算百分比，进度条会先冲到 100%、再掉回 0 重走一遍——用户看到的就是
「下载完成了怎么还在走」。所以这里必须按 requested_formats 的总大小累计。
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

# 必须在 import app.downloader 之前顶替 yt_dlp（downloader 顶层会 import 它）
if "yt_dlp" not in sys.modules:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = object
    sys.modules["yt_dlp"] = _stub

from app.downloader import DownloadManager, DownloadTask
from app.tasklog import TaskLogger


class ProgressHookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mgr = DownloadManager(Path(self._tmp.name))
        self.task = DownloadTask("t1", "https://example.com/v", "best")
        self.task.status = "downloading"

    def tearDown(self):
        self._tmp.cleanup()

    def test_single_stream_percentage(self):
        self.mgr._hook(
            {"status": "downloading", "downloaded_bytes": 500, "total_bytes": 1000,
             "info_dict": {"id": "x"}},
            self.task,
        )
        self.assertAlmostEqual(self.task.progress, 50.0, places=1)

    def test_two_streams_keep_progress_monotonic(self):
        info = {"requested_formats": [{"filesize": 800}, {"filesize": 200}]}
        # 视频轨下完
        self.mgr._hook({"status": "downloading", "downloaded_bytes": 800,
                        "total_bytes": 800, "info_dict": info}, self.task)
        self.assertAlmostEqual(self.task.progress, 80.0, places=1)
        self.mgr._hook({"status": "finished", "downloaded_bytes": 800,
                        "total_bytes": 800, "info_dict": info}, self.task)
        # 音频轨开始：不能掉回 0，而应该从 80% 继续
        self.mgr._hook({"status": "downloading", "downloaded_bytes": 100,
                        "total_bytes": 200, "info_dict": info}, self.task)
        self.assertAlmostEqual(self.task.progress, 90.0, places=1)

    def test_failure_summary_redacts_exception_fallback(self):
        """没有 yt-dlp 日志时，异常文本也不能把代理密码带到前端。"""
        task = DownloadTask("t-fail", "https://example.com/v", "best")
        summary = self.mgr._report_failure(
            task,
            Path(self._tmp.name) / task.id,
            TaskLogger(),
            RuntimeError("socks5h://user:secret@127.0.0.1:7890 failed"),
        )
        self.assertNotIn("secret", summary)
        self.assertIn("socks5h://***@127.0.0.1:7890", summary)

    def test_unknown_sizes_fall_back_to_per_stream(self):
        info = {"requested_formats": [{}, {}]}
        self.mgr._hook({"status": "downloading", "downloaded_bytes": 30,
                        "total_bytes": 100, "info_dict": info}, self.task)
        self.assertAlmostEqual(self.task.progress, 30.0, places=1)

    def test_postprocessing_stage_is_exposed(self):
        # 合并音视频时下载已经结束，但任务还没 finished：没有阶段提示就会
        # 一直显示「下载中 + 100%」的流光，看起来像卡住了
        cases = [
            ("Merger", "合并中"),
            ("ExtractAudio", "转码中"),
            ("VideoConvertor", "转码中"),
            ("Metadata", "后期处理中"),
        ]
        for postprocessor, expected in cases:
            with self.subTest(postprocessor=postprocessor):
                self.mgr._hook(
                    {"status": "started", "postprocessor": postprocessor}, self.task
                )
                self.assertEqual(self.task.stage, expected)
                self.mgr._hook(
                    {"status": "finished", "postprocessor": postprocessor}, self.task
                )
                self.assertIsNone(self.task.stage)

    def test_finished_stream_clears_live_counters(self):
        self.mgr._hook({"status": "finished", "downloaded_bytes": 800,
                        "info_dict": {}}, self.task)
        self.assertIsNone(self.task.speed)
        self.assertIsNone(self.task.eta)


if __name__ == "__main__":
    unittest.main()
