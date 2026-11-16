"""多流下载的音频轨 / 视频轨状态测试。

不启动真实 yt-dlp；直接喂 progress_hook 的典型数据结构，验证：
- 纯视频流标成“视频轨”，纯音频流标成“音频轨”；
- 两轨各自保留进度，单轨完成不会把另一轨覆盖掉；
- 单文件流不会伪装成双轨。
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path

if "yt_dlp" not in sys.modules:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = object
    sys.modules["yt_dlp"] = _stub

from app.downloader import DownloadManager, DownloadTask


class StreamTrackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.manager = DownloadManager(Path(self._tmp.name) / "dl")

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _task():
        task = DownloadTask("streams", "https://example.com/v", "best")
        task.status = "downloading"
        return task

    @staticmethod
    def _video_info():
        return {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none"}

    @staticmethod
    def _audio_info():
        return {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2"}

    def test_hook_keeps_video_and_audio_tracks_separate(self):
        task = self._task()

        self.manager._hook({
            "status": "downloading",
            "downloaded_bytes": 50,
            "total_bytes": 100,
            "info_dict": self._video_info(),
        }, task)
        self.manager._hook({
            "status": "downloading",
            "downloaded_bytes": 25,
            "total_bytes": 100,
            "info_dict": self._audio_info(),
        }, task)

        snapshot = task.to_dict()
        self.assertEqual(snapshot["current_stream"], "audio")
        self.assertEqual([s["kind"] for s in snapshot["streams"]], ["video", "audio"])
        self.assertEqual(snapshot["streams"][0]["label"], "视频轨")
        self.assertEqual(snapshot["streams"][0]["progress"], 50.0)
        self.assertEqual(snapshot["streams"][1]["label"], "音频轨")
        self.assertEqual(snapshot["streams"][1]["progress"], 25.0)

    def test_finished_video_track_stays_complete_while_audio_downloads(self):
        task = self._task()
        self.manager._hook({
            "status": "downloading",
            "downloaded_bytes": 100,
            "total_bytes": 100,
            "info_dict": self._video_info(),
        }, task)
        self.manager._hook({
            "status": "finished",
            "downloaded_bytes": 100,
            "info_dict": self._video_info(),
        }, task)
        self.manager._hook({
            "status": "downloading",
            "downloaded_bytes": 20,
            "total_bytes": 100,
            "info_dict": self._audio_info(),
        }, task)

        snapshot = task.to_dict()
        video = next(s for s in snapshot["streams"] if s["kind"] == "video")
        audio = next(s for s in snapshot["streams"] if s["kind"] == "audio")
        self.assertEqual(video["status"], "finished")
        self.assertEqual(video["progress"], 100.0)
        self.assertEqual(audio["status"], "downloading")
        self.assertEqual(audio["progress"], 20.0)
        self.assertEqual(snapshot["current_stream"], "audio")

    def test_single_combined_stream_does_not_look_like_two_tracks(self):
        task = self._task()
        self.manager._hook({
            "status": "downloading",
            "downloaded_bytes": 30,
            "total_bytes": 100,
            "info_dict": {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a"},
        }, task)

        snapshot = task.to_dict()
        self.assertEqual(snapshot["current_stream"], "combined")
        self.assertEqual([s["kind"] for s in snapshot["streams"]], ["combined"])
        self.assertEqual(snapshot["streams"][0]["label"], "单文件")

    def test_both_tracks_finished_then_merger_stage(self):
        task = self._task()
        for info, downloaded in (
            (self._video_info(), 100),
            (self._audio_info(), 100),
        ):
            self.manager._hook({
                "status": "downloading",
                "downloaded_bytes": downloaded,
                "total_bytes": 100,
                "info_dict": info,
            }, task)
            self.manager._hook({
                "status": "finished",
                "downloaded_bytes": downloaded,
                "info_dict": info,
            }, task)
        self.manager._hook({
            "status": "started",
            "postprocessor": "Merger",
        }, task)

        snapshot = task.to_dict()
        self.assertEqual(snapshot["stage"], "合并中")
        self.assertIsNone(snapshot["current_stream"])
        self.assertEqual(
            [s["status"] for s in snapshot["streams"]],
            ["finished", "finished"],
        )


if __name__ == "__main__":
    unittest.main()
