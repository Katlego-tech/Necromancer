"""Summon between the real enron and wayback spirits with the real tools (offline).
Pages are SYNTHETIC; Enron data is the synthetic fixture maildir."""
import asyncio
import json
import os
import tempfile
import unittest
from spirits.enron import ingest, spirit as enron_spirit
from spirits.wayback import spirit as wayback_spirit
from tests import fake_bedrock as fb
from tests.fake_bedrock import FakeFactory, wait_for
from tests.test_wayback_tools import URL, Fake, snap
from voice.emitter import Emitter
from voice.protocol_check import validate_event
from voice.session import VoiceSession
from voice.summon import Registry
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "maildir")
TS = "19990301000000"
class WaybackSummonTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "t.sqlite")
        ingest.build_index(FIXTURES, self.db)
        self.fetch = Fake(snap(TS))
        self.reg = Registry()
        self.reg.register(enron_spirit.make_spirit(self.db))
        self.reg.register(wayback_spirit.make_spirit(os.path.join(self.tmp.name, "cache"), self.fetch))
    def session(self, start, *scripts):
        factory = FakeFactory(*scripts)
        em = Emitter()
        s = VoiceSession(self.reg, start, factory, em, frame_seconds=0.002, filler_after=30,
                         tool_timeout=60, renew_after=1e9, summon_grace=0.05)
        return s, factory, em
    async def hand_over(self, start, target, persona_start, voice):
        script = [fb.on_text("go", fb.completion_start(),
                             fb.tool_use("summon_spirit", "tu-s", {"spirit": target}),
                             fb.assistant_text("Summoning.", "FINAL", "END_TURN"), fb.completion_end())]
        s, f, em = self.session(start, script)
        await s.start()
        await s.send_text("go")
        await wait_for(lambda: len(f.streams) == 2)
        await s.drain()
        new = f.streams[1]
        ps = new.sent[1]["promptStart"]
        self.assertEqual(ps["audioOutputConfiguration"]["voiceId"], voice)
        names = [t["toolSpec"]["name"] for t in ps["toolConfiguration"]["tools"]]
        self.assertIn("summon_spirit", names)
        inputs = [e["textInput"]["content"] for e in new.sent if "textInput" in e]
        self.assertEqual(inputs[0], self.reg.get(target).persona_prompt)
        self.assertTrue(any("go" in t for t in inputs[1:]))  # transcript carried into history
        self.assertEqual(s.spirit.id, target)
        self.assertEqual([e["spirit"] for e in em.events if e["type"] == "spirit"], [start, target])
        self.assertEqual(len([e for e in em.events if e["type"] == "appearance"]), 2)
        self.assertEqual(em.violations, [])
        await s.close()
        return names, inputs
    async def test_enron_summons_wayback(self):
        names, inputs = await self.hand_over("enron", "wayback", "enron", "tiffany")
        self.assertIn("read_snapshot", names)
        self.assertIn("list_snapshots", names)
        self.assertIn("Wayback Medium", inputs[0])
    async def test_wayback_summons_enron(self):
        names, inputs = await self.hand_over("wayback", "enron", "wayback", "matthew")
        self.assertIn("search_emails", names)
        self.assertIn("Enron Ghost", inputs[0])
    async def test_read_snapshot_reaches_model_and_panel_unchanged(self):
        script = [fb.on_text("page", fb.tool_use("read_snapshot", "tu-r", {"url": URL, "timestamp": TS}))]
        s, f, em = self.session("wayback", script)
        await s.start()
        await s.send_text("page please")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        payload = json.loads(st.tool_results()[0]["content"])
        self.assertIn("Sock puppet sale.", payload["content"])
        ev = [e for e in em.events if e["type"] == "evidence"][0]
        self.assertEqual(ev["kind"], "snapshot")
        self.assertEqual(ev["content"], payload["content"])
        self.assertEqual(ev["metadata"]["timestamp"], TS)
        self.assertIsNone(validate_event(ev))
        await s.close()
    async def test_cold_fetch_emits_cold_snapshot_evidence(self):
        self.fetch.responses = [snap(TS, status=404, body=b"")]
        script = [fb.on_text("page", fb.tool_use("read_snapshot", "tu-c", {"url": URL, "timestamp": TS}))]
        s, f, em = self.session("wayback", script)
        await s.start()
        await s.send_text("page please")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results(), timeout=10)
        self.assertEqual(json.loads(st.tool_results()[0]["content"])["summary"], "The trail goes cold.")
        ev = [e for e in em.events if e["type"] == "evidence"][0]
        self.assertEqual((ev["kind"], ev["source"], ev["content"]), ("snapshot", None, None))
        self.assertIsNone(validate_event(ev))
        await s.close()
if __name__ == "__main__":
    unittest.main()
