"""LiveController: limits, idle, disconnect, restart, concurrency, no leaks."""
import asyncio
import json
import os
import tempfile
import unittest

from tests import fake_bedrock as fb
from tests.fake_bedrock import FakeFactory, make_spirit, wait_for
from tests.test_voice_summon import echo_spirit
from tests.test_voice_usage import usage_event
from voice.emitter import Emitter
from voice.guard import CapStore, Limits
from voice.live import LiveController
from voice.session import VoiceSession
from voice.summon import Registry
from voice.usage import UsageLog


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class LiveBase(unittest.IsolatedAsyncioTestCase):
    def build(self, *, limits=None, scripts=(), registry=None, tick=0.002):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = Clock()
        self.factory = FakeFactory(*scripts)
        self.em = Emitter()
        reg = registry
        if reg is None:
            reg = Registry()
            reg.register(make_spirit("enron"))
        self.limits = limits or Limits(max_session_seconds=100, idle_seconds=50, disconnect_grace=0.02,
                                       daily_cap=10, total_cap=100)
        self.caps = CapStore(os.path.join(self.tmp.name, "caps.json"), self.limits)
        self.log_path = os.path.join(self.tmp.name, "usage.jsonl")
        self.printed = []
        self.sessions = []

        def make():
            s = VoiceSession(reg, "enron", self.factory, self.em, frame_seconds=0.002, clock=self.clock,
                             renew_after=1e9, reader_grace=0.5)
            self.sessions.append(s)
            return s
        self.live = LiveController(make, self.em, self.limits, self.caps, UsageLog(self.log_path),
                                   factory=self.factory, clock=self.clock, print_fn=self.printed.append,
                                   tick=tick)
        self.before = set(asyncio.all_tasks())

    def paused(self):
        return [e for e in self.em.events if e["type"] == "paused"]

    def log_lines(self):
        if not os.path.exists(self.log_path):
            return []
        return [json.loads(x) for x in open(self.log_path, encoding="utf-8")]

    async def assert_clean(self):
        await asyncio.sleep(0.05)
        leaked = [t for t in asyncio.all_tasks() - self.before
                  if not t.done() and t is not asyncio.current_task()]
        self.assertEqual(leaked, [])
        self.assertTrue(all(s.closed for s in self.factory.streams))

    def assert_one_end(self, reason):
        self.assertEqual([e["reason"] for e in self.paused()], [reason])
        self.assertEqual([x["end_reason"] for x in self.log_lines()], [reason])
        self.assertEqual(len(self.printed), 1)
        self.assertTrue(self.printed[0].startswith("usage: session="))


class LimitTest(LiveBase):
    async def test_max_session_closes_stream_with_paused_before_idle(self):
        self.build()
        self.assertTrue(await self.live.ensure_started())
        self.factory.streams[0].push(usage_event(3, 1, 4, 2))
        await wait_for(lambda: self.sessions[0].usage.streams() == 1)
        self.clock.t += 101
        self.sessions[0].last_activity = self.clock.t
        await wait_for(lambda: self.live.state == "off")
        st = self.factory.streams[0]
        self.assertTrue(st.closed)
        self.assertEqual(st.names()[-1], "sessionEnd")
        self.assert_one_end("session_limit")
        types = [(e["type"], e.get("reason") or e.get("state")) for e in self.em.events]
        self.assertLess(types.index(("paused", "session_limit")), len(types) - 1 - types[::-1].index(("state", "idle")))
        line = self.log_lines()[0]
        self.assertEqual(line["streams"], 1)
        self.assertEqual(line["usage"]["input"]["speechTokens"], 3)
        self.assertEqual(line["seconds"], 101.0)
        await self.assert_clean()

    async def test_idle_timeout_and_resets(self):
        self.build(limits=Limits(max_session_seconds=480, idle_seconds=50, disconnect_grace=0.02))
        await self.live.ensure_started()
        s, st = self.sessions[0], self.factory.streams[0]
        # raw mic audio alone is not activity
        s.feed_audio(b"\x7f\x7f" * 512)
        self.clock.t += 30
        await asyncio.sleep(0.03)
        self.assertEqual(self.live.state, "live")
        self.assertEqual(s.last_activity, 1000.0)
        # ASR user text resets
        st.push(fb.user_asr("hello"))
        await wait_for(lambda: s.last_activity == self.clock.t)
        # typed text resets
        self.clock.t += 30
        await s.send_text("typed")
        self.assertEqual(s.last_activity, self.clock.t)
        # spirit audio resets
        self.clock.t += 30
        st.push(fb.audio_out())
        await wait_for(lambda: s.last_activity == self.clock.t)
        self.assertEqual(self.live.state, "live")
        # blank ASR does not reset
        self.clock.t += 10
        st.push(fb.user_asr("   "))
        await asyncio.sleep(0.03)
        self.assertEqual(s.last_activity, self.clock.t - 10)
        self.clock.t += 45
        await wait_for(lambda: self.live.state == "off")
        self.assert_one_end("idle_timeout")
        await self.assert_clean()

    async def test_disconnect_grace_and_rejoin(self):
        self.build()
        await self.live.ensure_started()
        self.live.client_joined()
        self.live.client_left()
        self.live.client_joined()
        await asyncio.sleep(0.06)
        self.assertEqual(self.live.state, "live")
        self.live.client_left()
        await wait_for(lambda: self.live.state == "off")
        self.assert_one_end("disconnected")
        self.assertTrue(self.factory.streams[0].closed)
        await self.assert_clean()

    async def test_restart_after_limit_and_refused_by_cap(self):
        self.build(limits=Limits(max_session_seconds=100, idle_seconds=50, disconnect_grace=0.02,
                                 daily_cap=2, total_cap=100))
        await self.live.ensure_started()
        await self.live.stop("session_limit")
        self.assertTrue(await self.live.ensure_started())
        self.assertEqual(len(self.factory.streams), 2)
        self.assertEqual(self.live.status_event(), None)
        await self.live.stop("idle_timeout")
        self.assertFalse(await self.live.ensure_started())
        self.assertEqual(len(self.factory.streams), 2)
        self.assertEqual(self.paused()[-1]["reason"], "daily_cap")
        self.assertFalse(self.paused()[-1]["can_restart"])
        self.assertEqual(self.live.status_event()["reason"], "daily_cap")
        await self.assert_clean()

    async def test_new_session_clears_history(self):
        self.build()
        await self.live.ensure_started()
        await self.live.send_text("secret question")
        await self.live.stop("idle_timeout")
        await self.live.ensure_started()
        self.assertFalse(any(e["type"] == "transcript" and "secret" in e["text"] for e in self.em.events))
        await self.live.shutdown()
        await self.assert_clean()

    async def test_proxies_while_off_emit_paused(self):
        self.build()
        await self.live.send_text("hi")
        await self.live.summon("wayback")
        self.assertEqual([e["reason"] for e in self.paused()], ["not_started", "not_started"])
        self.assertEqual(self.factory.streams, [])
        self.assertEqual(self.live.status_event()["can_restart"], True)

    async def test_start_failure_counts_and_reports(self):
        self.build()

        class Boom:
            async def open(self):
                raise RuntimeError("nope")
        self.factory_real = self.factory
        self.sessions_before = 0
        reg = Registry()
        reg.register(make_spirit("enron"))
        live = LiveController(lambda: VoiceSession(reg, "enron", Boom(), self.em, frame_seconds=0.002),
                              self.em, self.limits, self.caps, UsageLog(self.log_path))
        self.assertFalse(await live.ensure_started())
        self.assertEqual(self.caps.snapshot()["daily_remaining"], self.limits.daily_cap - 1)
        self.assertEqual(self.paused()[-1]["reason"], "not_started")
        self.assertEqual(live.state, "off")


class StopTest(LiveBase):
    async def test_double_and_concurrent_stop_single_effect(self):
        self.build()
        await self.live.ensure_started()
        self.clock.t += 101
        await asyncio.gather(self.live.stop("shutdown"), self.live.stop("shutdown"),
                             self.live.stop("idle_timeout"))
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.paused()), 1)
        self.assertEqual(len(self.log_lines()), 1)
        self.assertEqual(len(self.printed), 1)
        await self.assert_clean()

    async def test_limit_racing_explicit_stop(self):
        self.build(tick=0.001)
        await self.live.ensure_started()
        self.clock.t += 101
        self.sessions[0].last_activity = self.clock.t
        await self.live.stop("disconnected")
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.paused()), 1)
        self.assertEqual(len(self.log_lines()), 1)
        await self.assert_clean()

    async def test_stop_during_blocked_handover(self):
        reg = Registry()
        reg.register(make_spirit("enron"))
        reg.register(echo_spirit())
        self.build(registry=reg)
        await self.live.ensure_started()
        gate = asyncio.Event()
        real_open = self.factory.open

        async def blocked_open():
            await gate.wait()
            return await real_open()
        self.factory.open = blocked_open
        summon = asyncio.create_task(self.live.summon("wayback"))
        await asyncio.sleep(0.03)
        self.assertTrue(self.sessions[0]._handover_active)
        await asyncio.wait_for(self.live.stop("session_limit"), 5)
        await asyncio.gather(summon, return_exceptions=True)
        self.assertEqual(len(self.factory.streams), 1)
        self.assert_one_end("session_limit")
        await self.assert_clean()

    async def test_racing_start_makes_one_session_and_one_count(self):
        self.build()
        results = await asyncio.gather(*[self.live.ensure_started() for _ in range(10)])
        self.assertTrue(all(results))
        self.assertEqual(len(self.factory.streams), 1)
        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(self.caps.snapshot()["daily_remaining"], self.limits.daily_cap - 1)
        await self.live.shutdown()
        self.assert_one_end("shutdown")
        await self.assert_clean()

    async def test_log_failure_is_swallowed_without_path(self):
        self.build()
        await self.live.ensure_started()
        os.makedirs(self.log_path)  # a directory: appending fails
        await self.live.stop("idle_timeout")
        errs = [e["message"] for e in self.em.events if e["type"] == "error"]
        self.assertEqual(errs, ["usage log could not be written"])
        self.assertEqual(self.live.state, "off")
        self.assertNotIn(self.tmp.name, json.dumps(self.em.events))


if __name__ == "__main__":
    unittest.main()
