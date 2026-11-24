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


if __name__ == "__main__":
    unittest.main()
