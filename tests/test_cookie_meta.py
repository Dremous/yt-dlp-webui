"""Cookie 文件元信息测试：只解析过期时间，不读取或回传 Cookie 值。"""

import tempfile
import unittest
from pathlib import Path

from app.cookie_meta import parse_cookie_metadata
from app.downloader import DownloadManager


def _line(name: str, expires: str, value: str = "secret-value") -> str:
    return f".example.com\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}"


class CookieMetadataTest(unittest.TestCase):
    def test_future_expiry_is_nearest_and_not_soon(self):
        now = 1_800_000_000
        text = "\n".join([
            "# Netscape HTTP Cookie File",
            _line("SID", str(now + 40 * 86400)),
            _line("PREF", str(now + 20 * 86400)),
        ])

        info = parse_cookie_metadata(text, now=now)

        self.assertEqual(info["cookie_count"], 2)
        self.assertEqual(info["auth_cookie_count"], 1)
        self.assertEqual(info["expired_count"], 0)
        self.assertEqual(info["nearest_expiry"], now + 20 * 86400)
        self.assertFalse(info["expiring_soon"])
        self.assertEqual(info["expiry_state"], "ok")
        self.assertNotIn("secret-value", str(info))

    def test_expired_cookie_wins_over_a_future_one(self):
        now = 1_800_000_000
        text = "\n".join([
            "# Netscape HTTP Cookie File",
            _line("SID", str(now - 1)),
            _line("PREF", str(now + 40 * 86400)),
        ])

        info = parse_cookie_metadata(text, now=now)

        self.assertEqual(info["expired_count"], 1)
        self.assertTrue(info["expiry_state"] == "expired")
        self.assertFalse(info["expiring_soon"])

    def test_session_cookie_is_not_treated_as_expired(self):
        text = "\n".join([
            "# Netscape HTTP Cookie File",
            _line("SID", "0"),
            _line("PREF", ""),
        ])

        info = parse_cookie_metadata(text, now=1_800_000_000)

        self.assertEqual(info["session_cookie_count"], 2)
        self.assertEqual(info["expired_count"], 0)
        self.assertEqual(info["expiry_state"], "session")
        self.assertIsNone(info["nearest_expiry"])

    def test_near_expiry_is_flagged(self):
        now = 1_800_000_000
        info = parse_cookie_metadata(
            "# Netscape HTTP Cookie File\n" + _line("SID", str(now + 3 * 86400)),
            now=now,
        )

        self.assertTrue(info["expiring_soon"])
        self.assertEqual(info["expiry_state"], "soon")

    def test_http_only_prefix_and_comments_are_handled(self):
        now = 1_800_000_000
        text = "\n".join([
            "# Netscape HTTP Cookie File",
            "# a normal comment",
            "#HttpOnly_.example.com\tTRUE\t/\tTRUE\t"
            f"{now + 10 * 86400}\tSID\tsecret-value",
        ])

        info = parse_cookie_metadata(text, now=now)

        self.assertEqual(info["cookie_count"], 1)
        self.assertEqual(info["nearest_expiry"], now + 10 * 86400)


class CookieInfoTest(unittest.TestCase):
    def test_manager_cookie_info_includes_expiry_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            cookie = Path(tmp) / "cookies.txt"
            manager = DownloadManager(Path(tmp), cookie)
            manager.save_cookie(
                ("# Netscape HTTP Cookie File\n"
                 + _line("SID", "0") + "\n").encode("utf-8")
            )

            info = manager.cookie_info()

        self.assertTrue(info["active"])
        self.assertEqual(info["cookie_count"], 1)
        self.assertEqual(info["expiry_state"], "session")
        self.assertNotIn("secret-value", str(info))


if __name__ == "__main__":
    unittest.main()
