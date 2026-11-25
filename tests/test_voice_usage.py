"""Usage metering and the usage log."""
import asyncio
import json
import os
import tempfile
import unittest

from tests import fake_bedrock as fb
from tests.fake_bedrock import FakeFactory, make_spirit, wait_for
from voice.emitter import Emitter
from voice.session import VoiceSession
from voice.summon import Registry
from voice.usage import UsageLog, UsageMeter, format_summary
from tests.test_voice_summon import echo_spirit


def usage_event(in_s, in_t, out_s, out_t):
    return [{"usageEvent": {"details": {"total": {
        "input": {"speechTokens": in_s, "textTokens": in_t},
        "output": {"speechTokens": out_s, "textTokens": out_t}}}}}]


class MeterTest(unittest.TestCase):
    def test_latest_total_per_stream_summed(self):
        m = UsageMeter()
        m.record(1, usage_event(1, 1, 1, 1)[0]["usageEvent"])
        m.record(1, usage_event(10, 5, 20, 7)[0]["usageEvent"])
        m.record(2, usage_event(3, 0, 4, 0)[0]["usageEvent"])
        t = m.totals()
        self.assertEqual(t["input"], {"speechTokens": 13, "textTokens": 5})
        self.assertEqual(t["output"], {"speechTokens": 24, "textTokens": 7})
        self.assertEqual(m.streams(), 2)

    def test_lower_later_total_keeps_max(self):
        m = UsageMeter()
        m.record(1, usage_event(10, 10, 10, 10)[0]["usageEvent"])
        m.record(1, usage_event(2, 20, 1, 1)[0]["usageEvent"])
        self.assertEqual(m.totals()["input"], {"speechTokens": 10, "textTokens": 20})

    def test_malformed_ignored(self):
        m = UsageMeter()
        for bad in (None, [], "x", {}, {"details": 5}, {"details": {"total": []}},
                    {"details": {"total": {"input": {"speechTokens": -1}}}},
                    {"details": {"total": {"input": {"speechTokens": "9"}}}},
                    {"details": {"total": {"output": {"textTokens": True}}}},
                    {"details": {"total": {"input": 4}}}):
            self.assertFalse(m.record(1, bad), bad)
        self.assertEqual(m.streams(), 0)
        self.assertEqual(m.totals()["input"], {"speechTokens": 0, "textTokens": 0})


class LogTest(unittest.TestCase):
    def test_line_has_exact_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "usage.jsonl")
            log = UsageLog(path)
            m = UsageMeter()
            m.record(1, usage_event(1, 2, 3, 4)[0]["usageEvent"])
            rec = log.append(seconds=12.34, streams=m.streams(), end_reason="idle_timeout", usage=m.totals())
            log.append(seconds=1, streams=0, end_reason="shutdown", usage=UsageMeter().totals())
            lines = open(path, encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 2)
        data = json.loads(lines[0])
        self.assertEqual(set(data), {"ts", "session_id", "seconds", "streams", "end_reason", "usage"})
        self.assertEqual(len(data["session_id"]), 32)
        self.assertRegex(data["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(data["usage"]["output"]["textTokens"], 4)
        self.assertEqual(format_summary(rec), "usage: session=%s reason=idle_timeout seconds=12.3 streams=1 "
                         "in_speech=1 in_text=2 out_speech=3 out_text=4" % data["session_id"][:8])


class SessionUsageTest(unittest.IsolatedAsyncioTestCase):
    async def test_usage_summed_across_handover_including_retired_stream(self):
        reg = Registry()
        reg.register(make_spirit("enron"))
        reg.register(echo_spirit())
        factory = FakeFactory()
        s = VoiceSession(reg, "enron", factory, Emitter(), frame_seconds=0.002, renew_after=1e9,
                         reader_grace=1.0)
        await s.start()
        old = factory.streams[0]
        old.push(usage_event(5, 1, 6, 2))
        await wait_for(lambda: s.usage.streams() == 1)
        orig_close = old.close

        async def close_with_usage():  # the retired stream reports its final total while closing
            old.push(usage_event(50, 4, 60, 8))
            await orig_close()
        old.close = close_with_usage
        await s.summon("wayback")
        new = factory.streams[1]
        new.push(usage_event(100, 10, 200, 20))
        await wait_for(lambda: s.usage.streams() == 2)
        await s.drain()
        await s.close()
        t = s.usage.totals()
        self.assertEqual(t["input"], {"speechTokens": 150, "textTokens": 14})
        self.assertEqual(t["output"], {"speechTokens": 260, "textTokens": 28})
        self.assertTrue(all(x.closed for x in factory.streams))

    async def test_final_usage_after_session_end_is_read(self):
        factory = FakeFactory()
        reg = Registry()
        reg.register(make_spirit("enron"))
        s = VoiceSession(reg, "enron", factory, Emitter(), frame_seconds=0.002, reader_grace=1.0)
        await s.start()
        st = factory.streams[0]
        orig_close = st.close

        async def close_with_usage():
            st.push(usage_event(7, 0, 8, 0))
            await orig_close()
        st.close = close_with_usage
        await s.close()
        self.assertEqual(s.usage.totals()["input"]["speechTokens"], 7)


if __name__ == "__main__":
    unittest.main()
