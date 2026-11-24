"""VoiceSession against the fake Bedrock stream."""
import asyncio
import json
import threading
import unittest

from tests import fake_bedrock as fb
from tests.fake_bedrock import FakeFactory, make_spirit, wait_for
from voice.emitter import Emitter
from voice.session import VoiceSession
from voice.summon import Registry


def build(*scripts, handler=None, **kw):
    reg = Registry()
    reg.register(make_spirit("enron", handler=handler))
    factory = FakeFactory(*scripts)
    em = Emitter()
    opts = dict(frame_seconds=0.002, filler_after=30, tool_timeout=60, renew_after=1e9)
    opts.update(kw)
    return VoiceSession(reg, "enron", factory, em, **opts), factory, em


def types(em, t):
    return [e for e in em.events if e["type"] == t]


class SessionTest(unittest.IsolatedAsyncioTestCase):
    async def test_startup_order_and_silence(self):
        s, f, em = build()
        await s.start()
        st = f.streams[0]
        await wait_for(lambda: len(st.of("audioInput")) >= 3)
        names = st.names()
        self.assertEqual(names[:3], ["sessionStart", "promptStart", "contentStart"])
        self.assertEqual(st.sent[2]["contentStart"]["role"], "SYSTEM")
        self.assertEqual(names[3], "textInput")
        self.assertEqual(names[5], "contentStart")  # audio block follows the system block
        self.assertEqual(st.sent[5]["contentStart"]["type"], "AUDIO")
        ps = st.sent[1]["promptStart"]
        self.assertEqual(ps["audioOutputConfiguration"]["voiceId"], "matthew")
        self.assertEqual([t["toolSpec"]["name"] for t in ps["toolConfiguration"]["tools"]], ["lookup"])
        self.assertTrue(all(p == bytes(2048) for p in st.audio_payloads()))
        self.assertEqual([e["state"] for e in types(em, "state")], ["idle", "listening"])
        self.assertEqual(em.violations, [])
        await s.close()

    async def test_mic_frames_sent_in_order_then_silence(self):
        s, f, em = build()
        await s.start()
        for i in range(1, 4):
            s.feed_audio(bytes([i]) * 4)
        st = f.streams[0]
        await wait_for(lambda: len(st.audio_payloads()) >= 8)
        real = [p for p in st.audio_payloads() if p != bytes(2048)]
        self.assertEqual(real, [b"\x01" * 4, b"\x02" * 4, b"\x03" * 4])
        await s.close()

    async def test_tool_round_trip_evidence_before_result(self):
        seen = []
        s, f, em = build(
            [fb.on_text("mail", fb.user_asr("find the mail"), fb.completion_start(),
                        fb.tool_use("lookup", "tu-1", {"query": "mail"}))])
        em.add_sink(lambda e: e["type"] == "evidence" and seen.append(len(f.streams[0].tool_results())))
        await s.start()
        st = f.streams[0]
        await s.send_text("mail please")
        await wait_for(lambda: st.tool_results())
        self.assertEqual(seen, [0])  # evidence went out before any toolResult was sent
        ev = types(em, "evidence")[0]
        self.assertEqual(ev["source"], "ev-0001")
        self.assertEqual(ev["content"], fb.EMAIL_RESULT["content"])
        tr = st.tool_results()[0]
        self.assertEqual(tr["content"], json.dumps(fb.EMAIL_RESULT))
        self.assertEqual(len(st.tool_results()), 1)
        cs = [e["contentStart"] for e in st.sent if "contentStart" in e and e["contentStart"]["type"] == "TOOL"]
        self.assertEqual(cs[0]["toolResultInputConfiguration"]["toolUseId"], "tu-1")
        self.assertEqual(em.violations, [])
        await s.close()

    async def test_tool_error_still_sends_one_result(self):
        def boom(args):
            raise ValueError("bad db")
        s, f, em = build([fb.on_text("x", fb.tool_use("lookup", "tu-e", {}))], handler=boom)
        await s.start()
        await s.send_text("x")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        self.assertIn("error", json.loads(st.tool_results()[0]["content"]))
        self.assertTrue(types(em, "error"))
        self.assertEqual(types(em, "evidence"), [])
        await s.close()

    async def test_unknown_tool_and_bad_args_send_result(self):
        s, f, em = build([fb.on_text("x", fb.tool_use("nope", "tu-n", {}))])
        await s.start()
        await s.send_text("x")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        self.assertIn("error", json.loads(st.tool_results()[0]["content"]))
        await s.close()

    async def test_tool_timeout_sends_one_result_and_filler(self):
        gate = threading.Event()
        def slow(args):
            gate.wait(5)
            return dict(fb.EMAIL_RESULT)
        s, f, em = build([fb.on_text("x", fb.tool_use("lookup", "tu-t", {}))], handler=slow,
                         filler_after=0.02, tool_timeout=0.1)
        await s.start()
        await s.send_text("x")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        gate.set()
        self.assertIn("timed out", json.loads(st.tool_results()[0]["content"])["error"])
        user_texts = [e["textInput"]["content"] for e in st.sent if "textInput" in e]
        self.assertTrue(any("still searching" in t for t in user_texts))
        self.assertEqual(len(st.tool_results()), 1)
        await s.close()

    async def test_transcript_speculative_then_final_and_history_final_only(self):
        s, f, em = build([fb.on_text("hi",
                                     fb.completion_start(), fb.user_asr("hi there"),
                                     fb.assistant_text("Hello the", "SPECULATIVE"),
                                     fb.assistant_text("Hello there friend.", "FINAL", "END_TURN"),
                                     fb.completion_end())])
        await s.start()
        await s.send_text("hi")
        await wait_for(lambda: any(e["type"] == "transcript" and e["role"] == "spirit" and e["final"]
                                   for e in em.events))
        spirit_ev = [e for e in em.events if e["type"] == "transcript" and e["role"] == "spirit"]
        self.assertEqual(spirit_ev[0]["text"], "Hello the")
        self.assertFalse(spirit_ev[0]["final"])
        self.assertEqual(spirit_ev[-1]["text"], "Hello there friend.")
        self.assertTrue(spirit_ev[-1]["final"])
        self.assertEqual({e["turn_id"] for e in spirit_ev}, {"s1"})
        self.assertEqual(s.transcript[-1], {"role": "spirit", "text": "Hello there friend."})
        hist = s._history()
        self.assertEqual([h["role"] for h in hist], ["USER", "ASSISTANT"])
        self.assertEqual(hist[1]["text"], "Hello there friend.")
        await s.close()

    async def test_state_sequence_with_speaking(self):
        s, f, em = build([fb.on_text("hi", fb.completion_start(), fb.audio_out(),
                                     fb.assistant_text("Hi.", "FINAL", "END_TURN"), fb.completion_end())])
        await s.start()
        await s.send_text("hi")
        await wait_for(lambda: [e["state"] for e in types(em, "state")][-1] == "listening"
                       and len(types(em, "state")) >= 4)
        self.assertEqual([e["state"] for e in types(em, "state")],
                         ["idle", "listening", "speaking", "listening"])
        self.assertGreater(s.audio_bytes, 0)
        await s.close()

    async def test_barge_in_while_tool_runs(self):
        gate = threading.Event()
        def slow(args):
            gate.wait(5)
            return dict(fb.EMAIL_RESULT)
        s, f, em = build([fb.on_text("x", fb.completion_start(), fb.audio_out(),
                                     fb.assistant_text("Searching", "FINAL"),
                                     fb.tool_use("lookup", "tu-b", {}))], handler=slow)
        cleared = []
        s.clear_sinks.append(lambda: cleared.append(1))
        await s.start()
        await s.send_text("x")
        st = f.streams[0]
        await wait_for(lambda: s.state == "speaking")
        st.push(fb.interrupted())
        await wait_for(lambda: cleared)
        await wait_for(lambda: s.state == "listening")
        self.assertEqual(cleared, [1])
        self.assertEqual(st.tool_results(), [])
        gate.set()
        await wait_for(lambda: st.tool_results())
        await asyncio.sleep(0.05)
        self.assertEqual(len(st.tool_results()), 1)
        self.assertEqual(len(types(em, "evidence")), 1)
        final = [e for e in em.events if e["type"] == "transcript" and e["role"] == "spirit" and e["final"]]
        self.assertEqual(final[-1]["text"], "Searching")
        await s.close()

    async def test_renewal_with_fake_clock(self):
        now = [0.0]
        s, f, em = build([], [], clock=lambda: now[0], renew_after=420)
        await s.start()
        s.feed_audio(b"\x07\x07")
        await asyncio.sleep(0.02)
        self.assertEqual(len(f.streams), 1)
        now[0] = 421.0
        await wait_for(lambda: len(f.streams) == 2)
        await s.drain()
        self.assertTrue(f.streams[0].closed)
        self.assertEqual([e["state"] for e in types(em, "state")], ["idle", "listening"])
        self.assertEqual(len(types(em, "spirit")), 1)
        await wait_for(lambda: len(f.streams[1].of("audioInput")) >= 2)
        await s.close()

    async def test_large_content_truncated_for_model_not_evidence(self):
        big = dict(fb.EMAIL_RESULT, content="x" * 50000)
        s, f, em = build([fb.on_text("x", fb.tool_use("lookup", "tu-big", {}))], handler=lambda a: big)
        await s.start()
        await s.send_text("x")
        st = f.streams[0]
        await wait_for(lambda: st.tool_results())
        sent = json.loads(st.tool_results()[0]["content"])
        self.assertTrue(sent["content"].endswith("[truncated: 40000 of 50000 chars]"))
        self.assertEqual(types(em, "evidence")[0]["content"], "x" * 50000)
        await s.close()


if __name__ == "__main__":
    unittest.main()
