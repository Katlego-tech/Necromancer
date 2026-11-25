"""Grid Ghost spirit tests. Fixtures are SYNTHETIC."""
import json
import os
import re
import tempfile
import unittest

from mascot.appearance import load_costume
from spirits.grid import spirit, tools
from voice.protocol_check import validate_event


def ev(e):
    return {"type": "evidence", "turn_id": "t1", **e}


class GridSpiritTest(unittest.TestCase):
    def test_persona_rules(self):
        p = spirit.PERSONA.lower()
        for w in ("amber", "orange", "yellow", "red", "costume", "glow", "halo"):
            self.assertIsNone(re.search(rf"\b{w}\b", p), w)
        self.assertIn("never mention, suggest or offer to control", p)
        self.assertNotIn("charge your", p)
        self.assertIn("replay of synthetic demo data", p)

    def test_evidence_valid_for_all_results(self):
        t = {d.name: d for d in spirit.make_tools()}
        results = [t["next_outage"].handler({"area": "Sandton (Demo Area 1)"}),
                   t["current_stage"].handler({}),
                   t["replay_outage"].handler({"fixture_id": "demo-stage2-soweto"}),
                   t["next_outage"].handler({"area": "Nowhere"})]
        for r in results:
            e = spirit.to_evidence(r)
            self.assertEqual(e["kind"], "grid")
            self.assertIsNone(validate_event(ev(e)), e)
        o = spirit.to_evidence(results[0])["metadata"]
        self.assertEqual((o["stage"], o["mode"]), ("4", "replay"))
        self.assertIn("synthetic", o["label"])
        s = spirit.to_evidence(results[1])["metadata"]
        self.assertEqual(s["area"], "not in status record")
        self.assertEqual(s["stage_updated"], "2026-11-30T17:40:00+02:00")
        self.assertEqual(spirit.to_evidence(results[3]),
                         {"kind": "grid", "source": None, "content": None})

    def test_tool_summary_is_spoken_but_content_is_raw(self):
        t = {d.name: d for d in spirit.make_tools()}
        r = t["next_outage"].handler({"area": "Sandton (Demo Area 1)"})
        self.assertIn("6 PM on 30 November 2026", r["summary"])
        self.assertNotIn("2026-11-30T", r["summary"])
        self.assertIn("2026-11-30T18:00:00+02:00", r["content"])

    def test_build_alert(self):
        a = spirit.build_alert()
        self.assertIn("stage 4", a["prompt"])
        self.assertIn("Sandton (Demo Area 1)", a["prompt"])
        self.assertIn("6 PM on 30 November 2026", a["prompt"])
        self.assertNotRegex(a["prompt"], r"\d{4}-\d{2}-\d{2}T")
        self.assertEqual((a["stage"], a["area"]), ("4", "Sandton (Demo Area 1)"))
        self.assertIsNone(validate_event(ev(a["evidence"])))
        self.assertIsNone(spirit.build_alert("Nowhere"))
        self.assertEqual(spirit.build_alert("soweto (demo area 2)")["stage"], "2")

    def test_garbage_time_is_cold(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.json")
            with open(p, "w") as f:
                json.dump({"events": [{"id": "a", "area": "X", "stage": "2", "start": "soon"}]}, f)
            self.assertIsNone(spirit.build_alert("X", p))

    def test_spirit_and_appearance(self):
        s = spirit.make_spirit()
        self.assertEqual((s.id, s.voice_id), ("grid", "tiffany"))
        self.assertEqual(load_costume(s.appearance_path)["animation"], "alert")
        self.assertEqual(tools.COLD, spirit.COLD)


if __name__ == "__main__":
    unittest.main()
