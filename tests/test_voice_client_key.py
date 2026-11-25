"""Wrong-code rate limiting key behind the local proxy (voice.server.client_key)."""
import unittest
from unittest import mock

from aiohttp import web

from voice import server


def req(remote, xff=None, trust=True):
    app = web.Application()
    app[server.TRUST_PROXY] = trust
    r = mock.Mock()
    r.remote = remote
    r.app = app
    r.headers = {} if xff is None else {"X-Forwarded-For": xff}
    return r


class ClientKeyTest(unittest.TestCase):
    def test_setting_off_always_uses_the_peer(self):
        self.assertEqual(server.client_key(req("127.0.0.1", "203.0.113.5", trust=False)), "127.0.0.1")

    def test_behind_loopback_proxy_uses_the_rightmost_entry(self):
        self.assertEqual(server.client_key(req("127.0.0.1", "203.0.113.5")), "203.0.113.5")
        self.assertEqual(server.client_key(req("::1", "2001:db8::7")), "2001:db8::7")

    def test_a_client_supplied_prefix_cannot_choose_the_key(self):
        # If a proxy ever appended instead of replacing, the forged left part is ignored.
        self.assertEqual(server.client_key(req("127.0.0.1", "1.2.3.4, 203.0.113.5")), "203.0.113.5")

    def test_non_loopback_peer_ignores_the_header(self):
        self.assertEqual(server.client_key(req("198.51.100.9", "203.0.113.5")), "198.51.100.9")

    def test_missing_or_malformed_header_falls_back_to_the_peer(self):
        for bad in (None, "", "   ", "not-an-ip", "203.0.113.5:443", "999.1.1.1", "<script>"):
            self.assertEqual(server.client_key(req("127.0.0.1", bad)), "127.0.0.1", bad)

    def test_unknown_peer(self):
        self.assertEqual(server.client_key(req(None, "203.0.113.5")), "?")


if __name__ == "__main__":
    unittest.main()
