"""Wayback tool tests. All pages and CDX rows are SYNTHETIC; the network is never touched
(except the optional smoke test, gated by NECROMANCER_LIVE_WAYBACK=1)."""
import json
import os
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from spirits.wayback import tools

COLD = {"source": None, "content": None, "summary": "The trail goes cold.", "evidence": []}
URL = "http://www.example.test/"
# Synthetic page.
HTML = (b"<html><head><title>Synthetic Pets</title><style>p{color:red}</style>"
        b"<script>var x=1;</script></head><body><h1>Welcome &amp; hello</h1>"
        b"<p>Sock   puppet\n sale.</p><div>Second<br>line</div></body></html>")
CDX = [["timestamp", "original", "mimetype", "statuscode"],
       ["19990201000000", URL, "text/html", "200"],
       ["19990601123456", URL, "text/html", "200"]]


class Fake:
    """Scripted fetch: a list of (status, final_url, body) or exceptions."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url):
        self.calls.append(url)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


def snap(ts, url=URL, body=HTML, status=200):
    return (status, f"https://web.archive.org/web/{ts}id_/{url}", body)


class WaybackToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = self.tmp.name
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, fetch, ts="19990301000000", url=URL):
        return tools.read_snapshot(url, ts, cache_dir=self.cache, fetch=fetch, sleep=self.sleeps.append)

    def lst(self, fetch, year=1999, url=URL):
        return tools.list_snapshots(url, year, cache_dir=self.cache, fetch=fetch, sleep=self.sleeps.append)

    # --- read_snapshot
    def test_cache_miss_then_hit_works_offline(self):
        f = Fake(snap("19990301000000"))
        r1 = self.read(f)
        self.assertEqual(len(f.calls), 1)
        self.assertFalse(r1["evidence"][0]["cached"])
        def boom(url):
            raise AssertionError("network used on cache hit")
        r2 = self.read(boom)
        self.assertTrue(r2["evidence"][0]["cached"])
        self.assertEqual(r1["content"], r2["content"])
        self.assertEqual(r1["source"], r2["source"])

    def test_fetch_uses_id_endpoint(self):
        f = Fake(snap("19990301000000"))
        self.read(f)
        self.assertEqual(f.calls[0], f"https://web.archive.org/web/19990301000000id_/{URL}")

    def test_source_has_url_and_exact_timestamp(self):
        r = self.read(Fake(snap("19990301000000")))
        self.assertEqual(r["source"], f"https://web.archive.org/web/19990301000000/{URL}")
        self.assertIn("19990301000000", r["summary"])
        self.assertIn(URL, r["summary"])

    def test_nearest_capture_timestamp_is_reported_not_requested(self):
        r = self.read(Fake(snap("19990415101112")), ts="19990301000000")
        self.assertIn("19990415101112", r["source"])
        self.assertNotIn("19990301000000", r["source"])
        e = r["evidence"][0]
        self.assertEqual(e["timestamp"], "19990415101112")
        self.assertEqual(e["requested_timestamp"], "19990301000000")
        self.assertIn("19990415101112", r["summary"])
        # cached copy keeps the served timestamp
        r2 = self.read(None, ts="19990301000000")
        self.assertEqual(r2["evidence"][0]["timestamp"], "19990415101112")

    def test_html_to_text(self):
        r = self.read(Fake(snap("19990301000000")))
        self.assertEqual(r["content"], "Synthetic Pets\nWelcome & hello\nSock puppet sale.\nSecond\nline")
        self.assertNotIn("var x", r["content"])
        self.assertNotIn("color:red", r["content"])

    def test_http_failure_is_cold_and_not_cached(self):
        self.assertEqual(self.read(Fake((404, "x", b""))), COLD)
        self.assertEqual(self.read(Fake(OSError("timeout"))), COLD)
        self.assertEqual(os.listdir(self.cache), [])

    def test_rate_limit_retries_with_backoff_then_succeeds(self):
        f = Fake((429, "x", b""), (503, "x", b""), snap("19990301000000"))
        r = self.read(f)
        self.assertEqual(len(f.calls), 3)
        self.assertEqual(self.sleeps, [1, 2])
        self.assertEqual(r["evidence"][0]["timestamp"], "19990301000000")

    def test_persistent_429_gives_cold_trail_without_raising(self):
        f = Fake((429, "x", b""))
        self.assertEqual(self.read(f), COLD)
        self.assertEqual(len(f.calls), tools.MAX_ATTEMPTS)

    def test_unparseable_served_url_is_cold(self):
        self.assertEqual(self.read(Fake((200, "https://elsewhere.test/", HTML))), COLD)

    def test_empty_page_is_cold(self):
        self.assertEqual(self.read(Fake(snap("19990301000000", body=b"<script>x</script>"))), COLD)

    def test_validation_returns_cold_trail_without_fetching(self):
        f = Fake(snap("19990301000000"))
        for url in ("", "ftp://x.test/", "not a url", "http://", None, "http://a b.test/"):
            self.assertEqual(self.read(f, url=url), COLD)
        for ts in ("1999", "199903010000", "1999030100000a", "", None, "19990301000000 "):
            self.assertEqual(self.read(f, ts=ts), COLD)
        for year in ("99", "abcd", None, 1800, "1999-"):
            self.assertEqual(self.lst(f, year=year), COLD)
        self.assertEqual(f.calls, [])

    def test_wrong_shape_cache_is_refetched_not_raised(self):
        import os
        for body in ({"snapshots": [{"timestamp": "19990201000000"}, 5]}, {"snapshots": "x"}):
            tools._cache_write(tools._cache_path(self.cache, "cdx", URL, "1999"), body)
            f = Fake((200, "x", json.dumps(CDX).encode()))
            self.assertEqual(len(self.lst(f)["evidence"]), 2)
            self.assertEqual(len(f.calls), 1)
        tools._cache_write(tools._cache_path(self.cache, "snap", URL, "19990301000000"),
                           {"timestamp": "19990301000000", "text": "x"})
        r = self.read(Fake(snap("19990301000000")))
        self.assertIn("19990301000000", r["source"])
    def test_unexpected_exception_returns_cold_trail(self):
        def boom(*a, **k):
            raise RuntimeError("x")
        self.assertEqual(self.read(boom), COLD)
        self.assertEqual(self.lst(boom), COLD)
    # --- list_snapshots
    def test_list_snapshots_returns_records_and_query(self):
        f = Fake((200, "x", json.dumps(CDX).encode()))
        r = self.lst(f)
        self.assertEqual([e["timestamp"] for e in r["evidence"]], ["19990201000000", "19990601123456"])
        self.assertEqual(r["source"], f"https://web.archive.org/web/19990201000000/{URL}")
        self.assertIn("19990601123456", r["content"])
        self.assertIn("2", r["summary"])
        q = parse_qs(urlsplit(f.calls[0]).query)
        self.assertEqual(q["from"], ["19990101"])
        self.assertEqual(q["to"], ["19991231"])
        self.assertEqual(q["output"], ["json"])
        self.assertEqual(q["filter"], ["statuscode:200", "mimetype:text/html"])
        self.assertTrue(f.calls[0].startswith("http://web.archive.org/cdx/search/cdx?"))

    def test_list_snapshots_cached_offline(self):
        self.lst(Fake((200, "x", json.dumps(CDX).encode())))
        def boom(url):
            raise AssertionError("network used")
        self.assertEqual(len(self.lst(boom)["evidence"]), 2)

    def test_empty_cdx_result_is_cold(self):
        self.assertEqual(self.lst(Fake((200, "x", b"[]"))), COLD)
        self.assertEqual(self.lst(Fake((200, "x", json.dumps(CDX[:1]).encode()))), COLD)
        self.assertEqual(self.lst(Fake((200, "x", b""))), COLD)

    def test_list_snapshots_http_failure_is_cold(self):
        self.assertEqual(self.lst(Fake((500, "x", b""))), COLD)
        self.assertEqual(self.lst(Fake((200, "x", b"not json"))), COLD)

    def test_malformed_cdx_rows_are_dropped(self):
        bad = CDX + [["oops", URL, "text/html", "200"], ["19990701000000"]]
        r = self.lst(Fake((200, "x", json.dumps(bad).encode())))
        self.assertEqual(len(r["evidence"]), 2)

    def test_user_agent_is_descriptive(self):
        self.assertIn("Necromancer", tools.USER_AGENT)


@unittest.skipUnless(os.environ.get("NECROMANCER_LIVE_WAYBACK") == "1",
                     "live smoke test; set NECROMANCER_LIVE_WAYBACK=1")
class LiveSmoke(unittest.TestCase):
    def test_live(self):
        with tempfile.TemporaryDirectory() as d:
            r = tools.list_snapshots("http://www.altavista.com/", 1999, cache_dir=d)
            self.assertNotEqual(r, COLD)
            ts = r["evidence"][0]["timestamp"]
            s = tools.read_snapshot("http://www.altavista.com/", ts, cache_dir=d)
            self.assertIsNotNone(s["content"])


if __name__ == "__main__":
    unittest.main()
