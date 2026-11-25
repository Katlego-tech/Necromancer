"""Spike (fake stream): the engine can start a spirit turn with no user prompt.

An engine-originated cross-modal USER text turn is sent while the mic audio block is open and
streaming; the spirit then speaks, and nothing lands in the transcript as a user turn.
"""
import asyncio
import unittest

from tests import fake_bedrock as fb
from voice.emitter import Emitter
from voice.session import VoiceSession
from voice.summon import Registry, Spirit

ALERT = "ALERT FROM THE SCHEDULE SYSTEM: stage 4; area Sandton (Demo Area 1)"
SPOKEN = "Warning. Stage 4 load-shedding for Sandton (Demo Area 1)."


class GridAlertSpikeTest(unittest.IsolatedAsyncioTestCase):
    async def test_engine_initiated_turn_without_user_prompt(self):
        script = [fb.on_text("ALERT FROM THE SCHEDULE SYSTEM",
                             fb.completion_start(), fb.assistant_text(SPOKEN, "SPECULATIVE"),
                             fb.audio_out(b"\x01\x02" * 64),
                             fb.assistant_text(SPOKEN, "FINAL", "END_TURN"), fb.completion_end())]
        factory = fb.FakeFactory(script)
        reg = Registry()
        reg.register(Spirit("grid", "You are the Grid Ghost.", "tiffany", (), None))
        em = Emitter()
        s = VoiceSession(reg, "grid", factory, em, frame_seconds=0.005)
        await s.start()
        stream = factory.streams[0]
        await fb.wait_for(lambda: len(stream.of("audioInput")) >= 3)   # silence already streaming
        before = len(stream.of("audioInput"))
        await s.inject_text(ALERT)
        await fb.wait_for(lambda: s.audio_bytes > 0 and any(
            e["type"] == "transcript" and e["role"] == "spirit" and e["final"] for e in em.events))
        await fb.wait_for(lambda: len(stream.of("audioInput")) > before + 2)  # audio kept flowing
        names = stream.names()
        self.assertLess(names.index("audioInput"), len(names) - 1 - names[::-1].index("textInput"))  # alert text after audio began
        blocks = [e["contentStart"] for e in stream.sent if "contentStart" in e]
        alert = [b for b in blocks if b["type"] == "TEXT" and b["role"] == "USER"][-1]
        self.assertTrue(alert["interactive"])
        self.assertEqual([t for t in s.transcript if t["role"] == "user"], [])
        self.assertFalse([e for e in em.events if e["type"] == "transcript" and e["role"] == "user"])
        self.assertEqual(s.transcript[-1], {"role": "spirit", "text": SPOKEN})
        await s.close()


if __name__ == "__main__":
    unittest.main()
