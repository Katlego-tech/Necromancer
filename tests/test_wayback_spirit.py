"""Wayback spirit module tests. Pages are SYNTHETIC; the network is never touched."""
import json
import os
import re
import tempfile
import unittest
from spirits.wayback import spirit, tools
from tests.test_wayback_tools import CDX, HTML, URL, Fake, snap
from voice.protocol_check import validate_event
class WaybackSpiritTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = self.tmp.name
    def tearDown(self):
        self.tmp.cleanup()
    def tools(self, fetch):
        return {t.name: t for t in spirit.make_tools(self.cache, fetch, lambda s: None)}
    def test_persona_rules_and_no_appearance_words(self):
        p = spirit.PERSONA
        for needle in (tools.COLD, "14-digit", "verbatim", "Digging through the archive."):
            self.assertIn(needle, p)
        words = set(re.findall(r"[a-z]+", p.lower()))
        banned = {"white", "black", "red", "green", "blue", "purple", "violet", "cyan", "pink",
                  "yellow", "orange", "gold", "silver", "grey", "gray", "costume", "costumes",
                  "palette", "halo", "hat", "robe", "props", "prop", "wearing", "glow", "glowing"}
        self.assertEqual(words & banned, set())
        self.assertNotIn("#", p)
    def test_voice_and_spirit_wiring(self):
        s = spirit.make_spirit()
        self.assertEqual((s.id, s.voice_id, s.appearance_path),
                         ("wayback", "tiffany", "mascot/costumes/wayback.json"))
        self.assertEqual([t.name for t in s.tools], ["list_snapshots", "read_snapshot"])
        for t in s.tools:
            self.assertEqual(t.input_schema["type"], "object")
            self.assertTrue(set(t.input_schema["required"]) <= set(t.input_schema["properties"]))
        self.assertEqual(s.tools[0].input_schema["required"], ["url", "year"])
        self.assertEqual(s.tools[1].input_schema["required"], ["url", "timestamp"])
    def test_read_handler_uses_injected_fetch_and_evidence_validates(self):
        f = Fake(snap("19990301000000"))
        t = self.tools(f)["read_snapshot"]
        r = t.handler({"url": URL, "timestamp": "19990301000000"})
        self.assertIn("Sock puppet sale.", r["content"])
        ev = t.to_evidence(r)
        self.assertEqual(ev["kind"], "snapshot")
        self.assertEqual(ev["metadata"], {"url": URL, "timestamp": "19990301000000"})
        self.assertIsNone(validate_event(dict(ev, type="evidence")))
    def test_list_handler_evidence_validates(self):
        f = Fake((200, "http://web.archive.org/cdx", json.dumps(CDX).encode()))
        t = self.tools(f)["list_snapshots"]
        r = t.handler({"url": URL, "year": "1999"})
        ev = t.to_evidence(r)
        self.assertEqual(ev["metadata"], {"url": URL, "timestamp": "19990201000000"})
        self.assertEqual(ev["content"], r["content"])
        self.assertIsNone(validate_event(dict(ev, type="evidence")))
    def test_every_cold_path_yields_cold_evidence_and_exact_summary(self):
        cases = [
            ("read_snapshot", {"url": "not a url", "timestamp": "19990301000000"}, Fake(snap("19990301000000"))),
            ("read_snapshot", {"url": URL, "timestamp": "1999"}, Fake(snap("19990301000000"))),
            ("read_snapshot", {"url": URL, "timestamp": "19990301000000"}, Fake(OSError("offline"))),
            ("read_snapshot", {"url": URL, "timestamp": "19990301000000"}, Fake(snap("19990301000000", status=404, body=b""))),
            ("read_snapshot", {"url": URL, "timestamp": "19990301000000"}, Fake(snap("19990301000000", body=b"<html><script>x</script></html>"))),
            ("list_snapshots", {"url": URL, "year": "abcd"}, Fake()),
            ("list_snapshots", {"url": URL, "year": "1999"}, Fake((200, "x", json.dumps(CDX[:1]).encode()))),
        ]
        for name, args, fetch in cases:
            with self.subTest(name=name, args=args):
                t = self.tools(fetch)[name]
                r = t.handler(args)
                self.assertEqual(r["summary"], spirit.COLD)
                self.assertEqual(spirit.COLD, "The trail goes cold.")
                ev = t.to_evidence(r)
                self.assertEqual(ev, {"kind": "snapshot", "source": None, "content": None})
                self.assertIsNone(validate_event(dict(ev, type="evidence")))
    def test_cache_hit_makes_no_fetch_call(self):
        f = Fake(snap("19990301000000"))
        self.tools(f)["read_snapshot"].handler({"url": URL, "timestamp": "19990301000000"})
        self.assertEqual(len(f.calls), 1)
        def boom(url):
            raise AssertionError("fetch must not be called on a cache hit")
        r = self.tools(boom)["read_snapshot"].handler({"url": URL, "timestamp": "19990301000000"})
        self.assertIn("Sock puppet sale.", r["content"])
    def test_make_spirit_defaults_work(self):
        self.assertEqual(spirit.make_spirit().id, "wayback")
if __name__ == "__main__":
    unittest.main()
