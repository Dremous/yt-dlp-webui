"""PO Token provider 配置测试：只在显式配置 URL 时才交给 yt-dlp。"""

import tempfile
import unittest
from pathlib import Path

from app.downloader import DownloadManager


class PotProviderOptionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_no_provider_url_keeps_ytdlp_default_behavior(self):
        manager = DownloadManager(Path(self._tmp.name))

        self.assertIsNone(manager.pot_provider_url)
        self.assertIsNone(manager._pot_extractor_args())

    def test_provider_url_is_passed_as_youtubepot_bgutilhttp_base_url(self):
        manager = DownloadManager(
            Path(self._tmp.name),
            pot_provider_url="http://bgutil-provider:4416/",
        )

        self.assertEqual(
            manager._pot_extractor_args(),
            {
                "youtubepot-bgutilhttp": {
                    "base_url": ["http://bgutil-provider:4416"],
                },
            },
        )

    def test_blank_provider_url_is_ignored(self):
        manager = DownloadManager(
            Path(self._tmp.name),
            pot_provider_url="   ",
        )

        self.assertIsNone(manager.pot_provider_url)
        self.assertIsNone(manager._pot_extractor_args())


if __name__ == "__main__":
    unittest.main()
