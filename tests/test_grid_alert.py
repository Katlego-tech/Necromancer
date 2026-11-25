"""Engine alert: summon the Grid Ghost, speak the engine-written alert, hand back."""
import asyncio
import json
import unittest

from tests import fake_bedrock as fb
from tests.fake_bedrock import FakeFactory, make_spirit, wait_for
from tests.test_voice_live_controller import LiveBase
from spirits.grid import spirit as grid
from voice.emitter import Emitter
from voice.session import VoiceSession
from voice.summon import Registry

ALERT = grid.build_alert()
ARGS = dict(evidence=ALERT["evidence"], line=ALERT["line"],
            expected=(ALERT["stage"], ALERT["area"], ALERT["spoken_start"]))
SPOKEN = (f"Warning. Stage {ALERT['stage']}; area {ALERT['area']}; start time "
          f"{ALERT['spoken_start']}. {grid.REPLAY_SENTENCE}")
TRIGGER = "ALERT FROM THE SCHEDULE SYSTEM"


def speak(text=SPOKEN):
    return fb.on_text(TRIGGER, fb.completion_start(), fb.assistant_text(text, "SPECULATIVE"),
                      fb.audio_out(b"\x01\x02" * 64), fb.assistant_text(text, "FINAL", "END_TURN"),
                      fb.completion_end())


def silent():
    return fb.on_text(TRIGGER)


def registry():
    reg = Registry()
    reg.register(make_spirit("enron"))
    reg.register(grid.make_spirit())
    return reg


def kinds(em):
    return [(e["type"], e.get("state") or e.get("spirit") or e.get("role")) for e in em.events]


class AlertTest(unittest.IsolatedAsyncioTestCase):
    async def start(self, *scripts, first="enron", **kw):
        self.factory = FakeFactory(*scripts)
        self.em = Emitter()
        kw.setdefault("frame_seconds", 0.003)
        self.s = VoiceSession(registry(), first, self.factory, self.em, renew_after=1e9, reader_grace=0.2, **kw)
        await self.s.start()
        self.addAsyncCleanup(self.s.close)
        return self.s

    async def finish(self):
        await wait_for(lambda: self.s._alert is None)

    async def test_summon_speak_return_event_order_and_no_user_turn(self):
        s = await self.start([], [speak()], [])
        self.assertEqual(await s.alert("grid", ALERT["prompt"], **ARGS), "started")
        await self.finish()
        got = kinds(self.em)
        want = [("spirit", "grid"), ("appearance", None), ("evidence", None), ("state", "summoning"),
                ("state", "alert"), ("state", "speaking"), ("transcript", "spirit"), ("state", "listening"),
                ("spirit", "enron")]
        it = iter(got[got.index(("spirit", "grid")):])
        self.assertTrue(all(w in it for w in want), got)
        self.assertEqual(s.spirit.id, "enron")
        self.assertEqual(len(self.factory.streams), 3)
        self.assertEqual(self.factory.streams[1].of("promptStart")[0]["audioOutputConfiguration"]["voiceId"], "tiffany")
        self.assertFalse([e for e in self.em.events if e["type"] == "transcript" and e["role"] == "user"])
        self.assertEqual(s.grounding_violations, [])
        self.assertEqual(s.transcript[-1], {"role": "spirit", "text": SPOKEN})
        self.assertEqual(self.em.violations, [])

    async def test_no_handover_when_grid_already_active(self):
        s = await self.start([speak()], first="grid")
        await s.alert("grid", ALERT["prompt"], **ARGS)
        await self.finish()
        self.assertEqual(len(self.factory.streams), 1)
        self.assertNotIn(("state", "summoning"), kinds(self.em))
        self.assertIn(("state", "alert"), kinds(self.em))

    async def test_unknown_spirit_and_busy(self):
        s = await self.start([], [speak()], [])
        self.assertEqual(await s.alert("nope", "x"), "unknown_spirit")
        self.assertEqual(await s.alert("grid", ALERT["prompt"], **ARGS), "started")
        self.assertEqual(await s.alert("grid", ALERT["prompt"], **ARGS), "busy")
        await self.finish()
        self.assertEqual(len(self.factory.streams), 3)

    async def test_waits_for_open_turn_then_barges_after_alert_wait(self):
        s = await self.start([], [speak()], [], alert_wait=0.15)
        first = self.factory.streams[0]
        first.push(fb.completion_start(), fb.assistant_text("I was saying", "SPECULATIVE"))
        await wait_for(lambda: s._turn is not None)
        await s.alert("grid", ALERT["prompt"], **ARGS)
        await asyncio.sleep(0.08)
        self.assertEqual(len(self.factory.streams), 1)       # still waiting
        await self.finish()
        self.assertEqual(len(self.factory.streams), 3)
        interrupted = [e for e in self.em.events if e["type"] == "transcript" and e["turn_id"] == "s1" and e["final"]]
        self.assertTrue(interrupted)

    async def test_waits_for_open_user_asr_block(self):
        s = await self.start([], [speak()], [], alert_wait=0.15)
        first = self.factory.streams[0]
        first.push([{"contentStart": {"contentId": "u", "type": "TEXT", "role": "USER",
                                      "additionalModelFields": json.dumps({"generationStage": "FINAL"})}}])
        await wait_for(lambda: first._q.empty())
        await asyncio.sleep(0.02)
        await s.alert("grid", ALERT["prompt"], **ARGS)
        await asyncio.sleep(0.08)
        self.assertEqual(len(self.factory.streams), 1)
        await self.finish()
        self.assertEqual(len(self.factory.streams), 3)

    async def test_no_audio_retries_once_then_scripted_line(self):
        s = await self.start([], [silent()], [], alert_speak_timeout=0.05)
        await s.alert("grid", ALERT["prompt"], **ARGS)
        await self.finish()
        grid_stream = self.factory.streams[1]
        texts = [t for t in grid_stream.of("textInput") if TRIGGER in t["content"]]
        self.assertEqual(len(texts), 2)
        lines = [e for e in self.em.events if e["type"] == "transcript" and e["text"] == ALERT["line"]]
        self.assertEqual(len(lines), 1)
        self.assertEqual(s.spirit.id, "enron")

    async def test_wrong_spoken_stage_is_a_violation_and_correct_paraphrase_is_not(self):
        s = await self.start([], [speak(SPOKEN.replace("Stage 4", "Stage 2"))], [])
        await s.alert("grid", ALERT["prompt"], **ARGS)
        await self.finish()
        self.assertTrue(any("stage" in v for v in s.grounding_violations), s.grounding_violations)
        s2 = await self.start([], [speak("Warning. Stage four, Sandton Demo Area 1, six PM on thirty November "
                                         "twenty twenty six. This is a replay of synthetic demo data.")], [])
        await s2.alert("grid", ALERT["prompt"], **ARGS)
        await self.finish()
        self.assertEqual(s2.grounding_violations, [])

    async def test_close_during_alert_leaves_no_task(self):
        s = await self.start([], [silent()], [])
        await s.alert("grid", ALERT["prompt"], **ARGS)
        await wait_for(lambda: len(self.factory.streams) >= 2)
        await s.close()
        self.assertIsNone(s._alert)
        self.assertTrue(all(st.closed for st in self.factory.streams))


class AlertLiveTest(LiveBase):
    def build_live(self, *scripts):
        reg = registry()
        self.build(scripts=scripts, registry=reg)
        self.live.registry = reg

    async def test_alert_does_not_touch_caps_or_idle_and_stop_is_clean(self):
        self.build_live([], [silent()], [])
        await self.live.ensure_started()
        caps, started = self.caps.snapshot(), self.sessions[0].last_activity
        self.assertEqual(await self.live.alert("grid", ALERT["prompt"], **ARGS), "started")
        await wait_for(lambda: len(self.factory.streams) >= 2)
        self.assertEqual(self.caps.snapshot(), caps)
        self.assertEqual(self.sessions[0].last_activity, started)
        self.assertEqual(await self.live.alert("grid", ALERT["prompt"], **ARGS), "busy")
        self.clock.t += 51
        await wait_for(lambda: self.live.state == "off")
        self.assert_one_end("idle_timeout")
        await self.assert_clean()

    async def test_stop_during_alert_one_paused_one_usage_line(self):
        self.build_live([], [silent()], [])
        await self.live.ensure_started()
        await self.live.alert("grid", ALERT["prompt"], **ARGS)
        await wait_for(lambda: len(self.factory.streams) >= 2)
        await self.live.stop("disconnected")
        self.assert_one_end("disconnected")
        await self.assert_clean()

    async def test_session_limit_still_applies(self):
        self.build_live([], [silent()], [])
        await self.live.ensure_started()
        await self.live.alert("grid", ALERT["prompt"], **ARGS)
        self.clock.t += 101
        self.sessions[0].last_activity = self.clock.t
        await wait_for(lambda: self.live.state == "off")
        self.assert_one_end("session_limit")
        await self.assert_clean()

    async def test_visual_only_without_live_session(self):
        self.build_live()
        caps = self.caps.snapshot()
        self.assertEqual(await self.live.alert("grid", ALERT["prompt"], **ARGS), "visual")
        types = [e["type"] for e in self.em.events]
        self.assertEqual(types, ["spirit", "appearance", "evidence", "transcript"])
        self.assertEqual(self.em.events[-1]["text"], ALERT["line"])
        self.assertEqual(self.factory.streams, [])
        self.assertEqual(self.caps.snapshot(), caps)
        self.assertEqual(self.live.state, "off")
        self.assertEqual(self.em.violations, [])


if __name__ == "__main__":
    unittest.main()
