"""局域网 Host 白名单的纯函数测试。"""

import unittest

from app.security import _host_from_header, host_allowed


class HostGuardTest(unittest.TestCase):
    def test_parses_host_with_port_and_ipv6_brackets(self):
        self.assertEqual(_host_from_header("192.168.1.10:8080"), "192.168.1.10")
        self.assertEqual(_host_from_header("[::1]:8080"), "::1")
        self.assertEqual(_host_from_header("Example.COM."), "example.com")

    def test_allows_localhost_and_private_ips(self):
        for host in ("localhost:8080", "127.0.0.1:8080", "[::1]:8080", "192.168.1.10"):
            with self.subTest(host=host):
                self.assertTrue(host_allowed(host))

    def test_rejects_unknown_domains_and_public_ips(self):
        self.assertFalse(host_allowed("evil.example"))
        self.assertFalse(host_allowed("8.8.8.8"))

    def test_env_allowlist_can_add_a_domain(self):
        self.assertTrue(host_allowed("webui.example", {"webui.example"}))
        self.assertFalse(host_allowed("other.example", {"webui.example"}))

    def test_empty_host_is_rejected(self):
        self.assertFalse(host_allowed(""))


if __name__ == "__main__":
    unittest.main()
