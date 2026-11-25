"""Grounding through the voice engine: tool output reaches the model and the panel unchanged."""
import json
import unittest

from tests import fake_bedrock as fb
from tests.fake_bedrock import wait_for
from tests.test_voice_session import build, types
from voice.protocol_check import validate_event


class VoiceGroundingTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_result_and_evidence_match_tool_output(self):
        s, f, em = build([fb.on_text("q", fb.tool_use("lookup", "t1", {"query": "q"}))])
        await s.start()
        await s.send_text("q")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        self.assertEqual(json.loads(st.tool_results()[0]["content"]), fb.EMAIL_RESULT)
        self.assertEqual(st.tool_results()[0]["content"], json.dumps(s.tool_results[0]))
        ev = types(em, "evidence")[0]
        self.assertEqual(ev["content"], fb.EMAIL_RESULT["content"])
        self.assertEqual(ev["metadata"]["sender"], "alice@example.invalid")
        self.assertIsNone(validate_event(ev))
        await s.close()

    async def test_cold_result_is_exact_in_result_and_evidence(self):
        s, f, em = build([fb.on_text("q", fb.tool_use("lookup", "t2", {}))],
                         handler=lambda a: dict(fb.COLD_RESULT))
        await s.start()
        await s.send_text("q")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        self.assertEqual(json.loads(st.tool_results()[0]["content"])["summary"], "The trail goes cold.")
        ev = types(em, "evidence")[0]
        self.assertEqual((ev["source"], ev["content"]), (None, None))
        self.assertIsNone(validate_event(ev))
        await s.close()

    async def test_cold_then_embellished_turn_is_flagged(self):
        s, f, em = build([fb.on_text("q", fb.tool_use("lookup", "t3", {}))],
                         handler=lambda a: dict(fb.COLD_RESULT))
        await s.start()
        await s.send_text("q")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        st.push(fb.assistant_text("It was probably about the merger.", "FINAL", "END_TURN"))
        await wait_for(lambda: s.grounding_violations)
        self.assertTrue(any(e["type"] == "error" and e["message"].startswith("grounding") for e in em.events))
        await s.close()

    async def test_cold_then_exact_line_is_clean(self):
        s, f, em = build([fb.on_text("q", fb.tool_use("lookup", "t4", {}))],
                         handler=lambda a: dict(fb.COLD_RESULT))
        await s.start()
        await s.send_text("q")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        st.push(fb.assistant_text("The trail goes cold.", "FINAL", "END_TURN"))
        await wait_for(lambda: s.transcript and s.transcript[-1]["role"] == "spirit")
        self.assertEqual(s.grounding_violations, [])
        await s.close()

    async def test_quote_must_come_from_tool_content(self):
        s, f, em = build([fb.on_text("q", fb.tool_use("lookup", "t5", {}))])
        await s.start()
        await s.send_text("q")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        st.push(fb.assistant_text('Alice wrote "need another review before Friday".', "FINAL"))
        st.push(fb.assistant_text('Alice wrote "everything is shredded and gone for good now".', "FINAL", "END_TURN"))
        await wait_for(lambda: s.grounding_violations)
        self.assertEqual(len(s.grounding_violations), 1)
        self.assertIn("shredded", s.grounding_violations[0])
        await s.close()

    def _snapshot_session(self, result, *scripts):
        from voice.emitter import Emitter
        from voice.session import VoiceSession
        from voice.summon import Registry, Spirit, ToolDef
        def snap_ev(r):
            return {"kind": "snapshot", "source": r["source"], "content": r["content"],
                    "metadata": {"url": "http://www.example.test/", "timestamp": "19990301000000"}}
        tool = ToolDef("read_snapshot", "Read.", {"type": "object"}, lambda a: dict(result), snap_ev)
        reg = Registry()
        reg.register(Spirit("wayback", "You are a test spirit.", "tiffany", (tool,), None))
        f, em = fb.FakeFactory(*scripts), Emitter()
        s = VoiceSession(reg, "wayback", f, em, frame_seconds=0.002, filler_after=30,
                         tool_timeout=60, renew_after=1e9)
        return s, f, em
    async def test_snapshot_text_reaches_model_transcript_and_panel_unchanged(self):
        text = "Welcome to the synthetic page.\nSock puppet sale."  # SYNTHETIC
        result = {"source": "https://web.archive.org/web/19990301000000/http://www.example.test/",
                  "content": text, "summary": "Archived snapshot.", "evidence": []}
        s, f, em = self._snapshot_session(result, [fb.on_text("q", fb.tool_use("read_snapshot", "t6", {}))])
        await s.start()
        await s.send_text("q")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        self.assertEqual(json.loads(st.tool_results()[0]["content"])["content"], text)
        ev = types(em, "evidence")[0]
        self.assertEqual((ev["kind"], ev["content"]), ("snapshot", text))
        self.assertIsNone(validate_event(ev))
        st.push(fb.assistant_text('It says "Sock puppet sale."', "FINAL", "END_TURN"))
        await wait_for(lambda: s.transcript and s.transcript[-1]["role"] == "spirit")
        self.assertIn("Sock puppet sale.", s.transcript[-1]["text"])
        self.assertEqual(s.grounding_violations, [])
        await s.close()
    async def test_overlong_snapshot_is_truncated_for_model_but_not_panel(self):
        from voice.session import MAX_TOOL_CONTENT
        text = "word " * (MAX_TOOL_CONTENT // 5 + 500)  # SYNTHETIC, over the model limit
        result = {"source": "ev-long", "content": text, "summary": "Long.", "evidence": []}
        s, f, em = self._snapshot_session(result, [fb.on_text("q", fb.tool_use("read_snapshot", "t7", {}))])
        await s.start()
        await s.send_text("q")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        sent = json.loads(st.tool_results()[0]["content"])["content"]
        self.assertIn("[truncated:", sent)
        self.assertTrue(sent.startswith(text[:MAX_TOOL_CONTENT]))
        self.assertEqual(types(em, "evidence")[0]["content"], text)
        await s.close()

if __name__ == "__main__":
    unittest.main()
