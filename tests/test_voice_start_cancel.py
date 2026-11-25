"""Lone-surrogate access codes and a start cancelled after taking a cap slot."""
import asyncio
import json
import unittest

from tests.test_voice_live_controller import LiveBase, make_spirit, Registry, VoiceSession, LiveController, UsageLog
from voice.guard import AccessGate


class SurrogateCodeTest(unittest.IsolatedAsyncioTestCase):
    def test_check_returns_false_for_lone_surrogates(self):
        gate = AccessGate("secret")
        for code in (json.loads('"\\ud800"'), "ab\udfffcd", "\ud83d", json.loads('"secret\\ud800"')):
            with self.subTest(code=code):
                self.assertFalse(gate.check(code))
        self.assertTrue(gate.check("secret"))

    async def test_attempt_counts_a_surrogate_code_as_a_failed_attempt(self):
        gate = AccessGate("secret", fail_limit=2, delay=0)
        self.assertEqual(await gate.attempt("r", json.loads('"\\ud800"')), "bad")
        self.assertEqual(await gate.attempt("r", "\udc00x"), "bad")
        self.assertTrue(gate.locked("r"))
        self.assertEqual(await gate.attempt("r", "secret"), "locked")

    async def test_gate_without_a_code_still_accepts_anything(self):
        self.assertTrue(AccessGate(None).check("\ud800"))


class HangingFactory:
    """A Bedrock factory whose open() never returns, so the start can be cancelled mid-way."""
    def __init__(self):
        self.opened = asyncio.Event()

    async def open(self):
        self.opened.set()
        await asyncio.sleep(3600)


class CancelledStartTest(LiveBase):
    def controller(self):
        self.build()
        reg = Registry()
        reg.register(make_spirit("enron"))
        hang = HangingFactory()
        live = LiveController(lambda: VoiceSession(reg, "enron", hang, self.em, frame_seconds=0.002),
                              self.em, self.limits, self.caps, UsageLog(self.log_path),
                              print_fn=self.printed.append)
        return live, hang

    async def test_cancelled_start_keeps_its_slot_and_leaves_a_usage_line(self):
        live, hang = self.controller()
        task = asyncio.create_task(live.ensure_started())
        await asyncio.wait_for(hang.opened.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.caps.snapshot()["daily_remaining"], self.limits.daily_cap - 1)
        lines = self.log_lines()
        self.assertEqual([r["end_reason"] for r in lines], ["start_cancelled"])
        self.assertEqual((lines[0]["streams"], lines[0]["seconds"]), (0, 0))
        self.assertTrue(self.printed and "start_cancelled" in self.printed[0])
        self.assertEqual(live.state, "off")

    async def test_cancel_while_the_cap_is_being_taken_is_still_logged(self):
        live, _ = self.controller()
        task = asyncio.create_task(live.ensure_started())
        await asyncio.sleep(0)  # the acquire thread has been scheduled, nothing has finished
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        used = self.limits.daily_cap - self.caps.snapshot()["daily_remaining"]
        self.assertEqual(len(self.log_lines()), used)
        self.assertIn(used, (0, 1))

    async def test_a_refused_start_writes_no_usage_line(self):
        self.build()
        reg = Registry()
        reg.register(make_spirit("enron"))
        for _ in range(self.limits.daily_cap):
            self.caps.try_acquire()
        live = LiveController(lambda: None, self.em, self.limits, self.caps, UsageLog(self.log_path))
        self.assertFalse(await live.ensure_started())
        self.assertEqual(self.log_lines(), [])


if __name__ == "__main__":
    unittest.main()
