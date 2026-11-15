"""输出格式预设的完整性检查。

FORMATS 是前端下拉框的唯一数据源（经 /api/formats 透出），id 缺字段或 label 重名
都要等到用户提交后才在下载时报错，所以在单元测试里挡住。

本地不装 yt-dlp，用最小 stub 顶替；这里只校验结构，不校验 yt-dlp 的格式表达能力
（格式串能否解析由真实的 yt-dlp 决定，属于集成测试范围）。
"""

import sys
import types
import unittest

# 必须在 import app.downloader 之前顶替 yt_dlp（downloader 顶层会 import 它）
if "yt_dlp" not in sys.modules:
    _stub = types.ModuleType("yt_dlp")
    _stub.YoutubeDL = object
    sys.modules["yt_dlp"] = _stub

from app.downloader import DEFAULT_FORMAT, FORMATS

# 历史上就存在的档位：前端默认值、API 调用方和文档都在用，不能删
# （best 不在此列：它是这轮新增的默认档，见 DEFAULT_FORMAT）
LEGACY_IDS = ("mp4", "mp3")

VIDEO_IDS = ("mp4", "mp4_2160", "mp4_1440", "mp4_1080", "mp4_720", "mp4_480",
             "mp4_avc", "best")
MP3_IDS = ("mp3", "mp3_320", "mp3_128")


class FormatsShapeTest(unittest.TestCase):
    def test_every_preset_has_label_and_format(self):
        for pid, preset in FORMATS.items():
            with self.subTest(preset=pid):
                self.assertIn("label", preset)
                self.assertTrue(preset["label"].strip())
                self.assertIn("options", preset)
                self.assertTrue(preset["options"].get("format", "").strip())

    def test_labels_are_unique(self):
        # 下拉框里出现两条一模一样的文案，用户没法分辨
        labels = [p["label"] for p in FORMATS.values()]
        self.assertEqual(len(labels), len(set(labels)))

    def test_preset_ids_are_slugs(self):
        # id 会直接出现在 API 的 body 里，保持小写字母 + 数字 + 下划线
        for pid in FORMATS:
            with self.subTest(preset=pid):
                self.assertRegex(pid, r"^[a-z][a-z0-9_]*$")

    def test_default_format_is_usable_and_first(self):
        # 界面的预选项就是列表第一项，默认值指错档位会让用户稀里糊涂下错产物
        self.assertIn(DEFAULT_FORMAT, FORMATS)
        self.assertEqual(next(iter(FORMATS)), DEFAULT_FORMAT)

    def test_legacy_ids_kept(self):
        for pid in LEGACY_IDS:
            with self.subTest(preset=pid):
                self.assertIn(pid, FORMATS)


class VideoPresetTest(unittest.TestCase):
    def test_video_presets_merge_into_mp4(self):
        # 不指定容器时 ffmpeg 会按编排输出 .mkv/.webm，用户拿到的就不是 mp4 了。
        # best 档例外：它明确保留原始格式。
        for pid in VIDEO_IDS:
            if pid == "best":
                continue
            with self.subTest(preset=pid):
                self.assertEqual(FORMATS[pid]["options"]["merge_output_format"], "mp4")

    def test_height_caps(self):
        for pid, height in (("mp4_2160", 2160), ("mp4_1440", 1440),
                            ("mp4_1080", 1080), ("mp4_720", 720), ("mp4_480", 480)):
            with self.subTest(preset=pid):
                self.assertIn(f"height<={height}", FORMATS[pid]["options"]["format"])

    def test_video_chain_has_fallback(self):
        # 只留一条选择链时，某档分辨率只有 AV1/VP9 就会整单失败
        for pid in VIDEO_IDS:
            if pid == "best":
                continue  # 最佳格式故意保留原始编码，不吃 mp4 约束
            with self.subTest(preset=pid):
                self.assertIn("/", FORMATS[pid]["options"]["format"])

    def test_avc_preset_prefers_h264(self):
        self.assertIn("vcodec^=avc1", FORMATS["mp4_avc"]["options"]["format"])


class AudioPresetTest(unittest.TestCase):
    def test_mp3_presets_extract_audio(self):
        for pid in MP3_IDS:
            with self.subTest(preset=pid):
                opts = FORMATS[pid]["options"]
                self.assertEqual(opts["format"], "bestaudio/best")
                pp = opts["postprocessors"][0]
                self.assertEqual(pp["key"], "FFmpegExtractAudio")
                self.assertEqual(pp["preferredcodec"], "mp3")
                self.assertIn(pp["preferredquality"], ("128", "192", "320"))

    def test_mp3_bitrates_match_labels(self):
        # 文案写的码率必须和实际转码码率一致，否则用户会被骗
        for pid in MP3_IDS:
            with self.subTest(preset=pid):
                quality = FORMATS[pid]["options"]["postprocessors"][0]["preferredquality"]
                self.assertIn(quality, FORMATS[pid]["label"])

    def test_m4a_preset_does_not_transcode(self):
        opts = FORMATS["m4a"]["options"]
        self.assertNotIn("postprocessors", opts)
        self.assertTrue(opts["format"].startswith("bestaudio"))

    def test_only_audio_presets_are_audio_only(self):
        for pid, preset in FORMATS.items():
            fmt = preset["options"]["format"]
            with self.subTest(preset=pid):
                if pid in MP3_IDS or pid == "m4a":
                    self.assertIn("bestaudio", fmt)
                else:
                    # 老的 mp4 档用的是 bestvideo 长写法，别把两种写法混为一谈
                    self.assertTrue("bv" in fmt or "bestvideo" in fmt)


if __name__ == "__main__":
    unittest.main()
