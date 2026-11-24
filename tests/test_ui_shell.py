import re
import shutil
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"


class UiShellTests(unittest.TestCase):
    def test_files_present(self):
        for name in ("index.html", "style.css", "EVENTS.md", "protocol.js", "render.js",
                     "mock.js", "client.js", "app.js", "mascot-slot.js", "voice-io.js",
                     "audio-worklet.js"):
            self.assertTrue((UI / name).is_file(), name)

    def test_no_unsafe_sinks_or_remote_resources(self):
        for p in list(UI.glob("*.js")) + [UI / "index.html", UI / "style.css"]:
            if p.name.endswith(".test.js"):
                continue
            text = p.read_text(encoding="utf8")
            self.assertIsNone(re.search(r"innerHTML|outerHTML|insertAdjacentHTML|\beval\s*\(", text), p.name)
            self.assertIsNone(re.search(r"https?://(?!example\.invalid)", text.replace("http://www.w3.org", "")), p.name)
        html = (UI / "index.html").read_text(encoding="utf8")
        self.assertIsNone(re.search(r"\son\w+=", html))
        self.assertIn('aria-live="polite"', html)
        for ref in ("../mascot/mascot.css", "../mascot/mascot.js", "voice-io.js", 'id="mic"', 'id="text-input"'):
            self.assertIn(ref, html)
        self.assertIn("prefers-reduced-motion", (UI / "style.css").read_text(encoding="utf8"))

    def test_events_md_has_examples(self):
        md = (UI / "EVENTS.md").read_text(encoding="utf8")
        for t in ("transcript", "state", "spirit", "appearance", "evidence", "error"):
            self.assertIn("### " + t, md)

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_node_suite(self):
        files = sorted(str(p) for p in (UI.parent / "tests" / "js").glob("*.test.js"))
        r = subprocess.run(["node", "--test", *files, str(UI / "protocol.test.js")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
