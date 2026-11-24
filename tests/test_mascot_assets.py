import os
import re
import shutil
import subprocess
import unittest

from mascot.tools.pngio import read_png

ROOT = os.path.join(os.path.dirname(__file__), "..", "mascot")
SPR = os.path.join(ROOT, "assets", "sprites")


class Sprites(unittest.TestCase):
    def test_idle_is_rgba_with_transparent_background(self):
        w, h, bpp, rows = read_png(os.path.join(SPR, "idle.png"))
        self.assertEqual(bpp, 4)
        for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (5, h // 2)):
            self.assertEqual(rows[y][x * 4 + 3], 0, (x, y))
        cx, cy = w // 2, h // 2
        self.assertEqual(rows[cy][cx * 4 + 3], 255)

    def test_idle_has_no_light_grey_halo(self):
        # semi-transparent edge pixels must not be background-coloured (225,225,232)
        w, h, _, rows = read_png(os.path.join(SPR, "idle.png"))
        bad = 0
        for r in rows[::4]:
            for x in range(w):
                R, G, B, A = r[x * 4:x * 4 + 4]
                if 0 < A < 255 and R > 215 and G > 215 and B > 215:
                    bad += 1
        self.assertLess(bad, 200)

    def test_web_copy_and_eye_mask_match_geometry(self):
        w, h, _, _ = read_png(os.path.join(SPR, "idle.png"))
        w2, h2, _, _ = read_png(os.path.join(SPR, "idle_web.png"))
        self.assertEqual((w2, h2), (w // 2, h // 2))
        mw, mh, _, rows = read_png(os.path.join(SPR, "eyes_patch.png"))
        self.assertEqual((mw, mh), (w, h))
        self.assertGreater(sum(1 for y in range(300, 460) for x in range(300, 650) if rows[y][x * 4 + 3]), 5000)


class StaticFiles(unittest.TestCase):
    def test_demo_references_existing_files(self):
        html = open(os.path.join(ROOT, "demo.html"), encoding="utf-8").read()
        for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
            self.assertFalse(ref.startswith(("http:", "https:", "//")), "no remote assets: " + ref)
            self.assertTrue(os.path.exists(os.path.join(ROOT, ref)), ref)
        for n in ("default", "enron", "wayback", "grid"):
            self.assertIn(n, html)

    def test_reduced_motion_paths_exist(self):
        css = open(os.path.join(ROOT, "mascot.css"), encoding="utf-8").read()
        js = open(os.path.join(ROOT, "mascot.js"), encoding="utf-8").read()
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn('data-reduced="true"', css)
        self.assertIn("reducedMotion", js)
        self.assertIn("is-fallback", css)

    def test_no_remote_urls_in_mascot_code(self):
        for fn in ("mascot.css", "mascot.js", "demo.html"):
            text = open(os.path.join(ROOT, fn), encoding="utf-8").read()
            text = text.replace("http://www.w3.org/2000/svg", "")
            self.assertNotRegex(text, r"https?://(?!127\.0\.0\.1|localhost)", fn)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class JsCore(unittest.TestCase):
    def test_node_core_tests(self):
        r = subprocess.run(["node", "--test", os.path.join(os.path.dirname(__file__), "js", "mascot_core.test.js")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
