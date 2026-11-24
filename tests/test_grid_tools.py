"""Grid tool tests. Fixtures are SYNTHETIC. No network is used."""
import json
import os
import socket
import tempfile
import unittest
from unittest import mock

from spirits.grid import tools

COLD = {"source": None, "content": None, "summary": "The trail goes cold.", "evidence": []}
FIX = {
    "synthetic": True,
    "status": {"stage": "3", "stage_updated": "2000-01-01T00:00:00+02:00"},
    "events": [
        {"id": "t1", "area": "Testville", "stage": "3",
         "start": "2000-01-01T18:07:00+02:00", "end": "2000-01-01T20:41:00+02:00"},
        {"id": "t2", "area": "Testville", "stage": "5",
         "start": "2000-01-01T09:13:00+02:00", "end": "2000-01-01T11:00:00+02:00"},
    ],
}


class GridToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "f.json")
        with open(cls.path, "w") as f:
            json.dump(FIX, f)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        p = mock.patch.object(socket.socket, "connect",
                              side_effect=AssertionError("network used"))
        p.start()
        self.addCleanup(p.stop)

    def test_contract_keys(self):
        for r in (tools.replay_outage("t1", self.path), tools.current_stage(self.path),
                  tools.next_outage("Testville", self.path), tools.replay_outage("x", self.path)):
            self.assertEqual(set(r), {"source", "content", "summary", "evidence"})

    def test_replay_summary_is_verbatim(self):
        r = tools.replay_outage("t1", self.path)
        self.assertEqual(r["source"], "t1")
        self.assertEqual(r["summary"], "Stage 3 load-shedding for Testville starts at "
                         "2000-01-01T18:07:00+02:00 and ends at 2000-01-01T20:41:00+02:00.")
        self.assertEqual(json.loads(r["content"]), FIX["events"][0])

    def test_next_outage_picks_earliest_start_verbatim(self):
        r = tools.next_outage("testville", self.path)
        self.assertEqual(r["source"], "t2")
        for v in ("5", "Testville", "2000-01-01T09:13:00+02:00"):
            self.assertIn(v, r["summary"])

    def test_current_stage(self):
        r = tools.current_stage(self.path)
        self.assertEqual(r["summary"], "The current stage is 3. Updated at 2000-01-01T00:00:00+02:00.")

    def test_unknown_fixture_or_area_is_cold(self):
        self.assertEqual(tools.replay_outage("nope", self.path), COLD)
        self.assertEqual(tools.next_outage("Nowhere", self.path), COLD)
        self.assertEqual(tools.next_outage("", self.path), COLD)
        self.assertEqual(tools.replay_outage("t1", "/nonexistent.json"), COLD)

    def test_missing_stage_is_cold(self):
        p = os.path.join(self.tmp.name, "n.json")
        with open(p, "w") as f:
            json.dump({"events": []}, f)
        self.assertEqual(tools.current_stage(p), COLD)

    def test_incomplete_record_is_cold_not_guessed(self):
        p = os.path.join(self.tmp.name, "i.json")
        with open(p, "w") as f:
            json.dump({"events": [{"id": "a", "area": "X", "stage": "2"}]}, f)
        self.assertEqual(tools.replay_outage("a", p), COLD)

    def test_summary_contains_only_record_values_and_no_device_advice(self):
        r = tools.replay_outage("t1", self.path)
        words = ("charge", "kettle", "turn off", "switch", "device", "unplug")
        for w in words:
            self.assertNotIn(w, r["summary"].lower())
        for part in ("Stage 3", "Testville", FIX["events"][0]["start"], FIX["events"][0]["end"]):
            self.assertIn(part, r["summary"])

    def test_live_mode_off_or_without_token_is_cold(self):
        called = []
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(tools.live_current_stage(lambda *a, **k: called.append(1)), COLD)
            self.assertEqual(tools.live_current_stage(enabled=True), COLD)
        with mock.patch.dict(os.environ, {"ESKOMSEPUSH_TOKEN": "x"}):
            self.assertEqual(tools.live_current_stage(lambda *a, **k: called.append(1)), COLD)
        self.assertEqual(called, [])

    def test_shipped_fixture_is_labeled_synthetic(self):
        with open(tools.DEFAULT_FIXTURE) as f:
            data = json.load(f)
        self.assertTrue(data["synthetic"])
        self.assertIn("SYNTHETIC", data["_label"])
        r = tools.next_outage("Sandton (Demo Area 1)")
        self.assertEqual(r["source"], "demo-stage4-sandton")
        self.assertEqual(tools.current_stage()["source"], "grid-replay-status")


if __name__ == "__main__":
    unittest.main()
