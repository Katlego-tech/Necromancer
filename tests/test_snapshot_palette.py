import json
import os
import tempfile
import unittest
from mascot import appearance
from mascot.tools import snapshot_palette as sp

# All HTML below is SYNTHETIC (written for these tests), not an archived page.
ATTRS = ('<html><body bgcolor="#FFFFCC" text="#000000" link="#99CC33" vlink="#FFCC00">'
         '<table bgcolor="#99cc33"><tr bgcolor="#ffcc00"></tr></table></body></html>')
CSS = ('<style>body{background:#fff8e0;color:#333} a{color:rgb(200,30,30)} '
       'h1{color:#36c}</style><p style="color:#36c">x</p>')
NAMED = '<body bgcolor="white" text="navy"><font color="red">a</font><font color="red">b</font></body>'


class Extract(unittest.TestCase):
    def test_attribute_colours(self):
        r = sp.extract(ATTRS)
        self.assertEqual(r["palette"]["body"], "#FFFFCC")
        self.assertEqual(r["palette"]["trim"], "#99CC33")
        self.assertEqual(r["palette"]["eyes"], "#FFCC00")

    def test_css_hex_short_hex_and_rgb(self):
        r = sp.extract(CSS)
        cols = set(r["colours"])
        self.assertIn("#3366CC", cols)
        self.assertIn("#C81E1E", cols)
        self.assertEqual(r["palette"]["body"], "#FFF8E0")

    def test_named_colours(self):
        r = sp.extract(NAMED)
        self.assertEqual(r["palette"]["trim"], "#FF0000")
        self.assertEqual(r["palette"]["eyes"], "#000080")

    def test_two_to_four_colours(self):
        many = "".join(f'<td bgcolor="#{c}">' for c in
                       ["ff0000", "00ff00", "0000ff", "ff00ff", "00ffff", "ffff00", "ffffcc"])
        r = sp.extract(many)
        self.assertTrue(2 <= len(r["colours"]) <= 4)

    def test_near_duplicates_are_merged(self):
        r = sp.extract('<td bgcolor="#ff0000"><td bgcolor="#fe0101"><td bgcolor="#0000ff">')
        self.assertEqual(len(r["colours"]), 2)

    def test_no_colours_returns_none(self):
        self.assertIsNone(sp.extract("<html><body><p>plain</p></body></html>"))
        self.assertIsNone(sp.extract('<body bgcolor="#ffffff" text="#000000">'))
        self.assertIsNone(sp.extract('<body bgcolor="#ff0000">'))

    def test_body_stays_light(self):
        r = sp.extract('<td bgcolor="#003366"><td bgcolor="#990000"><td bgcolor="#006600">')
        self.assertEqual(r["palette"]["body"], sp.FALLBACK_BODY)
        for html in (ATTRS, CSS, NAMED):
            self.assertGreaterEqual(sp._luminance(sp.extract(html)["palette"]["body"][1:]),
                                    sp.MIN_BODY_LUMINANCE)


class Costume(unittest.TestCase):
    def test_costume_validates_with_snapshot(self):
        cfg = sp.build_costume(sp.extract(ATTRS), "http://example.com/", "20000101000000")
        self.assertEqual(appearance.validate(cfg), [])
        self.assertEqual(cfg["snapshot"], {"url": "http://example.com/", "timestamp": "20000101000000"})
        self.assertNotIn("era_inspired", cfg)

    def test_costume_falls_back_to_era_inspired(self):
        cfg = sp.build_costume(None, "http://example.com/", "20000101000000")
        self.assertEqual(appearance.validate(cfg), [])
        self.assertIs(cfg["era_inspired"], True)
        self.assertNotIn("snapshot", cfg)
        self.assertEqual(cfg["label"], "Wayback Medium (era-inspired)")

    def test_cli_writes_costume(self):
        with tempfile.TemporaryDirectory() as d:
            raw, out = os.path.join(d, "a.html"), os.path.join(d, "w.json")
            open(raw, "w").write(ATTRS)
            self.assertEqual(sp.main([raw, "--url", "http://example.com/", "--timestamp",
                                      "20000101000000", "--write", out]), 0)
            self.assertEqual(appearance.validate(json.load(open(out))), [])


if __name__ == "__main__":
    unittest.main()
