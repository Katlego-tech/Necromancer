"""Handover summon: new Bedrock stream, same engine session, no drop."""
import asyncio
import json
import threading
import unittest

from tests import fake_bedrock as fb
from tests.fake_bedrock import FakeFactory, make_spirit, wait_for
from voice.emitter import Emitter
from voice.session import VoiceSession
from voice.summon import Registry, Spirit, ToolDef


def echo_spirit():
    echo = ToolDef("echo", "Echo.", {"type": "object"}, lambda a: {"source": None, "content": None,
                                                                  "summary": "echo", "evidence": []})
    return Spirit("wayback", "You are the dummy wayback spirit.", "amy", (echo,),
                  fb.os.path.join(fb.COSTUMES, "wayback.json"))


class SummonTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gate = threading.Event()
        def slow(args):
            self.gate.wait(5)
            return dict(fb.EMAIL_RESULT)
        self.reg = Registry()
        self.reg.register(make_spirit("enron", handler=slow))
        self.reg.register(echo_spirit())

    async def test_summon_swaps_stream_without_dropping_anything(self):
        script0 = [
            fb.on_text("hello", fb.completion_start(),
                       fb.assistant_text("Hello there.", "FINAL", "END_TURN"), fb.completion_end()),
            fb.on_text("find", fb.tool_use("lookup", "tu-s", {"query": "x"})),
        ]
        factory = FakeFactory(script0)
        em = Emitter()
        sinks, clears = [], []
        s = VoiceSession(self.reg, "enron", factory, em, frame_seconds=0.002, filler_after=30,
                         tool_timeout=60, renew_after=1e9)
        s.audio_sinks.append(sinks.append)
        s.clear_sinks.append(lambda: clears.append(1))
        await s.start()
        old = factory.streams[0]
        await s.send_text("hello")
        await wait_for(lambda: s.transcript and s.transcript[-1]["role"] == "spirit")
        await s.send_text("find it")
        await wait_for(lambda: any("textInput" in e and e["textInput"]["content"] == "find it" for e in old.sent))
        await asyncio.sleep(0.02)
        fed = [bytes([i]) * 6 for i in range(1, 41)]

        async def feeder():
            for fr in fed:
                s.feed_audio(fr)
                await asyncio.sleep(0.004)
        feed_task = asyncio.create_task(feeder())
        await asyncio.sleep(0.02)
        summon_task = asyncio.create_task(s.summon("wayback"))
        await wait_for(lambda: len(factory.streams) == 2)
        new = factory.streams[1]
        await summon_task
        await feed_task
        await asyncio.sleep(0.05)
        # old stream stays open while its toolResult is pending
        self.assertFalse(old.closed)
        self.assertEqual(old.tool_results(), [])
        self.gate.set()
        await s.drain()
        self.assertTrue(old.closed)
        self.assertEqual(len(old.tool_results()), 1)
        tr_idx = next(i for i, e in enumerate(old.sent) if "toolResult" in e)
        self.assertGreater(old.close_index, tr_idx)
        # the evidence for the old call still reached the panel
        self.assertEqual([e["source"] for e in em.events if e["type"] == "evidence"], ["ev-0001"])

        # new stream: wayback persona, voice, tools, history replayed before audio
        ps = new.sent[1]["promptStart"]
        self.assertEqual(ps["audioOutputConfiguration"]["voiceId"], "amy")
        names = [t["toolSpec"]["name"] for t in ps["toolConfiguration"]["tools"]]
        self.assertEqual(names, ["echo", "summon_spirit"])
        texts = [(e["contentStart"]["role"]) for e in new.sent if "contentStart" in e]
        self.assertEqual(texts[:3], ["SYSTEM", "USER", "ASSISTANT"])
        inputs = [e["textInput"]["content"] for e in new.sent if "textInput" in e]
        self.assertEqual(inputs[0], "You are the dummy wayback spirit.")
        self.assertIn("Hello there.", inputs[2])
        first_audio = next(i for i, e in enumerate(new.sent) if "audioInput" in e)
        last_text = max(i for i, e in enumerate(new.sent) if "textInput" in e)
        self.assertLess(last_text, first_audio)

        # mic frames: each exactly once, in order, across both streams
        real = [p for p in old.audio_payloads() + new.audio_payloads() if p != bytes(2048)]
        self.assertEqual(real, fed)

        # same emitter and sinks, events for the swap
        states = [e["state"] for e in em.events if e["type"] == "state"]
        self.assertEqual(states, ["idle", "listening", "summoning", "listening"])
        self.assertEqual([e["spirit"] for e in em.events if e["type"] == "spirit"], ["enron", "wayback"])
        self.assertEqual(len([e for e in em.events if e["type"] == "appearance"]), 2)
        self.assertEqual(em.violations, [])
        self.assertEqual(s.spirit.id, "wayback")
        self.assertEqual([t["role"] for t in s.transcript], ["user", "spirit", "user"])

        # reader moved to the new stream
        new.push(fb.completion_start(), fb.audio_out(b"\x09\x09"), fb.assistant_text("Hi.", "FINAL", "END_TURN"),
                 fb.completion_end())
        await wait_for(lambda: sinks)
        self.assertEqual(sinks, [b"\x09\x09"])
        await s.close()

    async def test_summon_unknown_spirit_reports_error(self):
        s = VoiceSession(self.reg, "enron", FakeFactory(), Emitter(), frame_seconds=0.002)
        await s.start()
        await s.summon("grid")
        self.assertTrue(any(e["type"] == "error" for e in s.emitter.events))
        self.assertEqual(s.spirit.id, "enron")
        await s.close()

    async def test_voice_triggered_summon_waits_for_turn_end(self):
        script0 = [fb.on_text("go", fb.completion_start(),
                              fb.tool_use("summon_spirit", "tu-v", {"spirit": "wayback"}),
                              fb.audio_out(), fb.assistant_text("Summoning.", "FINAL"))]
        factory = FakeFactory(script0)
        s = VoiceSession(self.reg, "enron", factory, Emitter(), frame_seconds=0.002, summon_grace=30)
        await s.start()
        await s.send_text("go")
        old = factory.streams[0]
        await wait_for(lambda: old.tool_results())
        self.assertEqual(json.loads(old.tool_results()[0]["content"]), {"summoning": "wayback"})
        await asyncio.sleep(0.03)
        self.assertEqual(len(factory.streams), 1)  # still speaking: no handover yet
        old.push(fb.assistant_text("Done.", "FINAL", "END_TURN"), fb.completion_end())
        await wait_for(lambda: len(factory.streams) == 2)
        await s.drain()
        self.assertEqual(s.spirit.id, "wayback")
        await s.close()


if __name__ == "__main__":
    unittest.main()
