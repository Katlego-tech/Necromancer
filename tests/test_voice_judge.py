"""T7.10 judge access: second code, own caps and limits, shared attempt limiting, switches, expiry."""
import asyncio
import base64
import contextlib
import datetime
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
from tests.test_voice_access import CODE, Clock, MainTest, auth, close_code, start
from tests.test_voice_live_controller import LiveBase
from tests.test_voice_server import read_events
from voice import server
from voice.guard import (DEFAULT_JUDGE_EXPIRES, AccessGate, CapStore, Limits, MIN_JUDGE_CODE_CHARS,
                         parse_expiry)
from voice.protocol_check import validate_event
from voice.server import GATE, SESSION
from voice.usage import UsageLog

JUDGE = "judge-Zk93-pLm4-77Qa"
UTC = datetime.timezone.utc


def gate(**kw):
    kw.setdefault("delay", 0)
    kw.setdefault("clock", Clock())
    return AccessGate(CODE, judge_code=JUDGE, **kw)


class RoleOfTest(unittest.IsolatedAsyncioTestCase):
    async def test_each_code_maps_to_its_role_and_others_fail(self):
        g = gate()
        self.assertEqual(await g.attempt_role("r", CODE), ("ok", "public"))
        self.assertEqual(await g.attempt_role("r", JUDGE), ("ok", "judge"))
        self.assertEqual(await g.attempt_role("r", "wrong"), ("bad", None))
        self.assertEqual(await g.attempt_role("r", None), ("bad", None))
        self.assertEqual(await g.attempt_role("r", json.loads('"\\ud800"')), ("bad", None))
        self.assertEqual(await g.attempt_role("r", "a" * 257), ("bad", None))
        self.assertEqual(await g.attempt("r", "bad"), "bad")      # the fifth failure
        self.assertEqual(await g.attempt("r", JUDGE), "locked")   # budget spent, even for a right code

    async def test_equal_or_short_judge_code_refused(self):
        with self.assertRaises(ValueError) as cm:
            AccessGate(CODE, judge_code=CODE)
        self.assertNotIn(CODE, str(cm.exception))
        with self.assertRaises(ValueError):
            AccessGate("  " + JUDGE + " ", judge_code=JUDGE)
        with self.assertRaises(ValueError) as cm:
            AccessGate(CODE, judge_code="x" * (MIN_JUDGE_CODE_CHARS - 1))
        self.assertIn(str(MIN_JUDGE_CODE_CHARS), str(cm.exception))
        AccessGate(CODE, judge_code="x" * MIN_JUDGE_CODE_CHARS)

    async def test_both_digests_compared_for_every_candidate(self):
        g = gate()
        calls = []
        real = __import__("hmac").compare_digest

        def spy(a, b):
            calls.append((len(a), len(b)))
            return real(a, b)
        with mock.patch("voice.guard.hmac.compare_digest", spy):
            for cand in ("x", CODE, JUDGE):
                g.role_of(cand)
        self.assertEqual(calls, [(32, 32)] * 6)
        with mock.patch("voice.guard.hmac.compare_digest") as cd:
            g.role_of("a" * 257)
            g.role_of(None)
        cd.assert_not_called()

    async def test_no_raw_code_kept(self):
        g = gate()
        self.assertNotIn(JUDGE, repr(vars(g)))
        self.assertNotIn(CODE, repr(vars(g)))

    async def test_attempt_limit_is_shared_between_the_two_codes(self):
        g = gate()
        for i in range(5):
            self.assertEqual(await g.attempt("r", "bad" + str(i)), "bad")
        self.assertEqual(await g.attempt("r", JUDGE), "locked")
        self.assertEqual(await g.attempt("r", CODE), "locked")
        g2 = gate(global_limit=3)
        for i in range(3):
            await g2.attempt(f"r{i}", "bad")
        self.assertEqual(await g2.attempt("fresh", JUDGE), "locked")

    async def test_judge_only_gate_needs_no_public_code(self):
        g = AccessGate(None, judge_code=JUDGE, delay=0)
        self.assertTrue(g.required)
        self.assertEqual(await g.attempt_role("r", JUDGE), ("ok", "judge"))
        self.assertEqual(await g.attempt_role("r", CODE), ("bad", None))


class SwitchAndExpiryTest(unittest.IsolatedAsyncioTestCase):
    async def test_startup_switch_disables_one_role_only(self):
        g = gate(disabled=["judge"])
        self.assertEqual(await g.attempt_role("r", JUDGE), ("bad", None))
        self.assertEqual(await g.attempt_role("r", CODE), ("ok", "public"))
        g = gate(disabled=["public"])
        self.assertEqual(await g.attempt_role("r", CODE), ("bad", None))
        self.assertEqual(await g.attempt_role("r", JUDGE), ("ok", "judge"))

    async def test_file_switch_works_on_a_running_gate_and_can_be_removed(self):
        with tempfile.TemporaryDirectory() as d:
            g = gate(control_dir=d)
            self.assertEqual(await g.attempt_role("r", JUDGE), ("ok", "judge"))
            open(os.path.join(d, "disable-judge"), "w").close()
            self.assertFalse(g.allowed("judge"))
            self.assertTrue(g.allowed("public"))
            self.assertEqual(await g.attempt_role("r", JUDGE), ("bad", None))
            os.unlink(os.path.join(d, "disable-judge"))
            self.assertEqual(await g.attempt_role("r", JUDGE), ("ok", "judge"))
            open(os.path.join(d, "disable-public"), "w").close()
            self.assertEqual(await g.attempt_role("r", CODE), ("bad", None))

    async def test_missing_control_dir_means_not_switched_off(self):
        with tempfile.TemporaryDirectory() as d:
            g = gate(control_dir=os.path.join(d, "does-not-exist"))
            self.assertTrue(g.allowed("judge"))
            self.assertTrue(g.allowed("public"))
    async def test_unreadable_control_dir_fails_closed_for_both_roles(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        with tempfile.TemporaryDirectory() as d:
            control = os.path.join(d, "control")
            os.mkdir(control)
            open(os.path.join(control, "disable-judge"), "w").close()
            g = gate(control_dir=control)
            os.chmod(control, 0)
            try:
                self.assertFalse(g.allowed("judge"))      # a disable file we cannot read still disables
                self.assertFalse(g.allowed("public"))     # and we cannot tell about the other role either
            finally:
                os.chmod(control, 0o700)                  # so the temp dir can be removed
    async def test_expiry(self):
        now = [datetime.datetime(2027, 1, 29, 23, 59, tzinfo=datetime.timezone(datetime.timedelta(hours=-8)))]
        g = gate(judge_expires=parse_expiry(None), now=lambda: now[0])
        self.assertEqual(await g.attempt_role("r", JUDGE), ("ok", "judge"))
        now[0] = datetime.datetime(2027, 1, 30, 7, 59, 58, tzinfo=UTC)
        self.assertTrue(g.allowed("judge"))
        now[0] = datetime.datetime(2027, 1, 30, 8, 0, 0, tzinfo=UTC)
        self.assertEqual(await g.attempt_role("r", JUDGE), ("bad", None))
        self.assertEqual(await g.attempt_role("r", CODE), ("ok", "public"))

    def test_parse_expiry(self):
        self.assertEqual(parse_expiry(None), parse_expiry(DEFAULT_JUDGE_EXPIRES))
        self.assertEqual(parse_expiry("").utcoffset(), datetime.timedelta(hours=-8))
        self.assertIsNone(parse_expiry("none"))
        self.assertEqual(parse_expiry("2027-02-01T00:00:00Z").utcoffset(), datetime.timedelta(0))
        for bad in ("2027-01-29", "2027-01-29T10:00:00", "soon"):
            with self.assertRaises(ValueError):
                parse_expiry(bad)


class RoleCapsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "var", "caps.json")
        self.lim = Limits(daily_cap=2, total_cap=3, judge_daily_cap=4, judge_total_cap=5)

    def test_roles_have_separate_counters(self):
        s = CapStore(self.path, self.lim)
        self.assertEqual([s.try_acquire()[0] for _ in range(3)], [True, True, False])
        self.assertEqual(s.try_acquire(), (False, "daily_cap"))
        self.assertEqual([s.try_acquire("judge")[0] for _ in range(5)], [True] * 4 + [False])
        self.assertEqual(s.try_acquire("judge"), (False, "daily_cap"))
        self.assertEqual(s.snapshot(), {"daily_remaining": 0, "total_remaining": 1})
        self.assertEqual(s.snapshot("judge"), {"daily_remaining": 0, "total_remaining": 1})

    def test_judge_total_cap_and_day_rollover_and_restart(self):
        clock = [datetime.datetime(2026, 12, 20, 23, 59, tzinfo=UTC)]
        s = CapStore(self.path, self.lim, now=lambda: clock[0])
        for _ in range(4):
            self.assertTrue(s.try_acquire("judge")[0])
        clock[0] = datetime.datetime(2026, 12, 21, 0, 1, tzinfo=UTC)
        s = CapStore(self.path, self.lim, now=lambda: clock[0])   # a restart
        self.assertEqual(s.snapshot("judge"), {"daily_remaining": 4, "total_remaining": 1})
        self.assertEqual(s.try_acquire("judge"), (True, None))
        self.assertEqual(s.try_acquire("judge"), (False, "total_cap"))
        self.assertEqual(s.snapshot()["total_remaining"], 3)   # public counters untouched

    def test_public_exhaustion_never_blocks_judge(self):
        s = CapStore(self.path, Limits(daily_cap=0, total_cap=0, judge_daily_cap=1, judge_total_cap=1))
        self.assertEqual(s.try_acquire(), (False, "total_cap"))
        self.assertEqual(s.try_acquire("judge"), (True, None))

    def test_old_file_without_judge_keys_reads_as_zero(self):
        os.makedirs(os.path.dirname(self.path))
        today = datetime.datetime.now(UTC).strftime("%Y-%m-%d")
        with open(self.path, "w") as f:
            json.dump({"total": 3, "day": today, "day_count": 1}, f)
        s = CapStore(self.path, self.lim)
        self.assertEqual(s.snapshot("judge"), {"daily_remaining": 4, "total_remaining": 5})
        self.assertEqual(s.snapshot(), {"daily_remaining": 1, "total_remaining": 0})
        self.assertTrue(s.try_acquire("judge")[0])
        with open(self.path) as f:
            self.assertEqual(json.load(f)["total"], 3)

    def test_corrupt_judge_counter_fails_closed(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"total": 0, "day": "x", "day_count": 0, "judge_total": -1}, f)
        s = CapStore(self.path, self.lim)
        self.assertEqual(s.try_acquire("judge"), (False, "total_cap"))
        self.assertEqual(s.snapshot("judge"), {"daily_remaining": 0, "total_remaining": 0})

    def test_threads_of_both_roles_get_exactly_their_caps(self):
        lim = Limits(daily_cap=3, total_cap=3, judge_daily_cap=5, judge_total_cap=5)
        got = {"public": [], "judge": []}

        def work(role):
            got[role].append(CapStore(self.path, lim).try_acquire(role)[0])
        threads = [threading.Thread(target=work, args=(r,)) for r in ("public", "judge") for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((sum(got["public"]), sum(got["judge"])), (3, 5))

    def test_limits_bounds(self):
        for bad in ({"judge_max_session_seconds": 481}, {"judge_max_session_seconds": 0},
                    {"judge_idle_seconds": 0}, {"judge_daily_cap": -1}, {"judge_total_cap": -1}):
            with self.assertRaises(ValueError):
                Limits(**bad)
        Limits(judge_max_session_seconds=480)
        lim = Limits()
        self.assertEqual((lim.session_seconds("judge"), lim.idle_limit("judge")), (300, 90))
        self.assertGreater(lim.session_seconds("judge"), lim.session_seconds("public"))
        self.assertLessEqual(lim.session_seconds("judge"), 480)


class JudgeControllerTest(LiveBase):
    def judge_build(self, **kw):
        self.build(limits=Limits(max_session_seconds=100, idle_seconds=50, disconnect_grace=0.02,
                                 daily_cap=1, total_cap=1, judge_max_session_seconds=300,
                                 judge_idle_seconds=200, judge_daily_cap=2, judge_total_cap=3), **kw)

    async def test_judge_starts_while_public_caps_are_exhausted(self):
        self.judge_build()
        self.assertTrue(await self.live.ensure_started("public"))
        await self.live.stop("session_limit")
        self.assertFalse(await self.live.ensure_started("public"))
        self.assertEqual(self.paused()[-1]["reason"], "total_cap")
        self.assertTrue(await self.live.ensure_started("judge"))
        self.assertEqual(self.caps.snapshot("judge")["daily_remaining"], 1)
        self.assertEqual(self.caps.snapshot()["total_remaining"], 0)   # public counters unchanged by judge
        await self.live.stop("session_limit")
        self.assertEqual([x["role"] for x in self.log_lines()], ["public", "judge"])
        self.assertIn("role=judge", self.printed[-1])
        self.assertNotIn("role=", self.printed[0])

    async def test_judge_cap_hit_pauses_with_video_link_and_public_unaffected(self):
        self.judge_build()
        self.live.video_url = "https://example.com/demo.mp4"
        for _ in range(2):
            self.assertTrue(await self.live.ensure_started("judge"))
            await self.live.stop("session_limit")
        self.assertFalse(await self.live.ensure_started("judge"))
        ev = self.em.events[-1]
        self.assertEqual((ev["reason"], ev["can_restart"], ev["video_url"], ev["_audience"]),
                         ("daily_cap", False, "https://example.com/demo.mp4", "judge"))
        self.assertIsNone(validate_event(ev))
        self.assertEqual(self.live.status_event("judge")["reason"], "daily_cap")
        self.assertEqual(self.live.status_event("judge")["video_url"], "https://example.com/demo.mp4")
        self.assertEqual(self.live.status_event("public")["reason"], "session_limit")
        self.assertTrue(await self.live.ensure_started("public"))
        await self.live.stop("session_limit")

    async def test_judge_total_cap_message(self):
        self.build(limits=Limits(disconnect_grace=0.02, judge_total_cap=0))
        self.assertFalse(await self.live.ensure_started("judge"))
        self.assertEqual(self.live.status_event("judge")["reason"], "total_cap")

    async def test_concurrent_starts_of_both_roles_make_one_session_and_one_count(self):
        self.judge_build()
        res = await asyncio.gather(self.live.ensure_started("judge"), self.live.ensure_started("public"),
                                   self.live.ensure_started("judge"))
        self.assertEqual(res, [True, True, True])
        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(self.caps.snapshot("judge")["daily_remaining"], 1)
        self.assertEqual(self.caps.snapshot()["daily_remaining"], 1)
        await self.live.stop("shutdown")
        self.assertEqual([x["role"] for x in self.log_lines()], ["judge"])

    async def test_judge_session_uses_the_judge_limits(self):
        self.judge_build()
        self.live.client_joined()
        self.assertTrue(await self.live.ensure_started("judge"))
        self.clock.t += 150           # past the public limit (100) and idle (50), inside the judge's
        self.sessions[0].last_activity = self.clock.t - 100
        await asyncio.sleep(0.05)
        self.assertEqual(self.live.state, "live")
        self.clock.t += 151           # judge session limit is 300
        self.sessions[0].last_activity = self.clock.t
        await asyncio.sleep(0.05)
        for _ in range(100):
            if self.live.state == "off":
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.live.state, "off")
        self.assertEqual([(x["end_reason"], x["role"]) for x in self.log_lines()], [("session_limit", "judge")])

    async def test_judge_idle_timeout(self):
        self.judge_build()
        self.live.client_joined()
        self.assertTrue(await self.live.ensure_started("judge"))
        self.clock.t += 201
        for _ in range(200):
            if self.live.state == "off":
                break
            await asyncio.sleep(0.01)
        self.assertEqual([(x["end_reason"], x["role"]) for x in self.log_lines()], [("idle_timeout", "judge")])

    async def test_switch_or_expiry_refuses_start_and_ends_a_live_session(self):
        self.judge_build()
        allowed = {"judge": True, "public": True}
        self.live.role_allowed = lambda role: allowed[role]
        self.assertTrue(await self.live.ensure_started("judge"))
        allowed["judge"] = False
        for _ in range(200):
            if self.live.state == "off":
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.live.state, "off")
        self.assertEqual(self.log_lines()[-1]["end_reason"], "bad_code")
        slots = self.caps.snapshot("judge")["total_remaining"]
        self.assertFalse(await self.live.ensure_started("judge"))
        self.assertEqual(self.caps.snapshot("judge")["total_remaining"], slots)   # no slot taken
        self.assertEqual(self.paused()[-1]["reason"], "bad_code")
        self.assertEqual(self.live.status_event("judge")["reason"], "bad_code")
        self.assertTrue(await self.live.ensure_started("public"))
        await self.live.stop("shutdown")

    async def test_cancelled_judge_start_is_logged_as_judge(self):
        self.judge_build()
        task = asyncio.create_task(self.live.ensure_started("judge"))
        await asyncio.sleep(0)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        for line in self.log_lines():
            self.assertEqual(line["role"], "judge")


class JudgeUsageLogTest(unittest.TestCase):
    def test_role_field_and_old_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "u.jsonl")
            log = UsageLog(path)
            zero = {"input": {"speechTokens": 0, "textTokens": 0}, "output": {"speechTokens": 0, "textTokens": 0}}
            log.append(seconds=1, streams=1, end_reason="idle_timeout", usage=zero)
            log.append(seconds=1, streams=1, end_reason="idle_timeout", usage=zero, role="judge")
            with open(path, "a") as f:   # a line written before judge access
                f.write(json.dumps({"ts": "2026-12-01T00:00:00Z", "session_id": "a" * 32, "seconds": 1.0,
                                    "streams": 1, "end_reason": "shutdown", "usage": zero}) + "\n")
            rows = [json.loads(x) for x in open(path)]
            with self.assertRaises(ValueError):
                log.append(seconds=1, streams=1, end_reason="x", usage=zero, role=JUDGE)
        self.assertEqual([r.get("role", "public") for r in rows], ["public", "judge", "public"])
        self.assertEqual(set(rows[1]), {"ts", "session_id", "seconds", "streams", "end_reason", "role", "usage"})
        from voice.usage import format_summary
        self.assertTrue(format_summary(rows[2]).startswith("usage: session=aaaaaaaa"))
        self.assertTrue(format_summary(rows[1]).endswith(" role=judge"))


class JudgeServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = None

    async def asyncTearDown(self):
        if self.client:
            await self.client.close()
            self.client._tmp.cleanup()

    async def connect(self, path, code):
        ws = await self.client.ws_connect(path)
        await ws.receive()
        await auth(ws, code)
        return ws

    async def test_judge_code_starts_a_judge_session_when_public_caps_are_zero(self):
        limits = Limits(disconnect_grace=0.05, daily_cap=0, total_cap=0, judge_daily_cap=2, judge_total_cap=2)
        self.client = await start(gate=gate(), public=True, limits=limits)
        ctl = self.client.server.app[SESSION]
        pub_events = await self.connect("/events", CODE)
        jud_events = await self.connect("/events", JUDGE)
        pub_io = await self.connect("/io", CODE)
        await pub_io.send_str(json.dumps({"type": "start"}))
        got = await read_events(pub_events, lambda e: e["type"] == "paused" and e["reason"] != "not_started")
        self.assertEqual(got[-1]["reason"], "total_cap")
        self.assertNotIn("_audience", got[-1])
        jud_io = await self.connect("/io", JUDGE)
        await jud_io.send_str(json.dumps({"type": "start"}))
        async with asyncio.timeout(3):
            while ctl.state != "live":
                await asyncio.sleep(0.01)
        self.assertEqual(ctl.role, "judge")
        self.assertEqual(ctl.caps.snapshot("judge")["daily_remaining"], 1)
        await jud_io.close()
        await pub_io.close()
        async with asyncio.timeout(3):
            while ctl.state != "off":
                await asyncio.sleep(0.01)
        await pub_events.close()
        await jud_events.close()

    async def test_events_connection_gets_only_its_own_roles_cap_pause(self):
        limits = Limits(disconnect_grace=0.05, daily_cap=5, total_cap=5, judge_daily_cap=0, judge_total_cap=0)
        self.client = await start(gate=gate(), public=True, limits=limits)
        pub = await self.connect("/events", CODE)
        jud = await self.connect("/events", JUDGE)
        await read_events(pub, lambda e: e["type"] == "paused")
        got = await read_events(jud, lambda e: e["type"] == "paused")
        self.assertEqual(got[-1]["reason"], "total_cap")        # judge status: its own caps
        io = await self.connect("/io", JUDGE)
        await io.send_str(json.dumps({"type": "start"}))
        got = await read_events(jud, lambda e: e["type"] == "paused")
        self.assertEqual(got[-1]["reason"], "total_cap")
        self.assertNotIn("_audience", got[-1])
        with self.assertRaises(TimeoutError):                   # the public page saw nothing
            async with asyncio.timeout(0.3):
                await pub.receive()
        await io.close()

    async def test_status_for_late_joiner_uses_role(self):
        limits = Limits(daily_cap=0, total_cap=0, judge_daily_cap=3, judge_total_cap=3)
        self.client = await start(gate=gate(), public=True, limits=limits)
        pub = await self.connect("/events", CODE)
        jud = await self.connect("/events", JUDGE)
        self.assertEqual((await read_events(pub, lambda e: e["type"] == "paused"))[-1]["reason"], "total_cap")
        self.assertEqual((await read_events(jud, lambda e: e["type"] == "paused"))[-1]["reason"], "not_started")

    async def test_wrong_and_switched_off_judge_code_get_the_ordinary_bad_code(self):
        with tempfile.TemporaryDirectory() as d:
            self.client = await start(gate=gate(control_dir=d), public=True)
            ws = await self.connect("/io", "wrong")
            bad = json.loads((await ws.receive()).data)
            self.assertEqual(await close_code(ws), 4401)
            open(os.path.join(d, "disable-judge"), "w").close()
            ws = await self.connect("/io", JUDGE)
            off = json.loads((await ws.receive()).data)
            self.assertEqual(await close_code(ws), 4401)
            self.assertEqual(off, bad)

    async def test_judge_locked_out_by_shared_failures(self):
        self.client = await start(gate=gate(), public=True)
        for _ in range(5):
            ws = await self.connect("/io", "bad")
            await close_code(ws)
        ws = await self.connect("/io", JUDGE)
        self.assertIn("try later", json.loads((await ws.receive()).data)["message"])
        self.assertEqual(await close_code(ws), 4429)

    async def test_alert_endpoint_accepts_judge_code_header(self):
        self.client = await start(gate=gate(), public=True)
        r = await self.client.post("/alert", headers={"X-Necro-Code": "bad"})
        self.assertEqual(r.status, 401)
        r = await self.client.post("/alert", headers={"X-Necro-Code": JUDGE})
        self.assertNotEqual(r.status, 401)

    async def test_judge_code_is_in_no_output_channel(self):
        needles = [JUDGE, base64.b64encode(JUDGE.encode()).decode(), hashlib.sha256(JUDGE.encode()).hexdigest()]
        out, err = io.StringIO(), io.StringIO()
        frames = []
        tmp = tempfile.TemporaryDirectory()
        record = os.path.join(tmp.name, "record.jsonl")
        with self.assertLogs(level="DEBUG") as logs, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            __import__("logging").getLogger().debug("probe")
            client = self.client = await start(gate=gate(), public=True, record_path=record,
                                               limits=Limits(disconnect_grace=0.05))
            ctl = client.server.app[SESSION]
            for attempt in (JUDGE, "wrong", JUDGE):
                for path in ("/events", "/io"):
                    ws = await client.ws_connect(path)
                    frames.append((await ws.receive()).data)
                    await auth(ws, attempt)
                    if path == "/io" and attempt == JUDGE:
                        await ws.send_str(json.dumps({"type": "start"}))
                    try:
                        async with asyncio.timeout(0.5):
                            while True:
                                msg = await ws.receive()
                                if msg.type.name == "TEXT":
                                    frames.append(msg.data)
                                elif msg.type.name != "BINARY":
                                    break
                    except TimeoutError:
                        pass
                    await ws.close()
            await ctl.stop("session_limit")
            tmpdir = client._tmp.name
            files = [open(os.path.join(tmpdir, n)).read() for n in os.listdir(tmpdir)]
            self.assertTrue(any('"role": "judge"' in f for f in files))
        files.append(open(record).read())
        blob = "\n".join(frames + files + [out.getvalue(), err.getvalue()] + logs.output)
        for n in needles:
            self.assertNotIn(n, blob)
        self.assertNotIn("_audience", blob)
        tmp.cleanup()


class JudgeMainTest(MainTest):
    def args(self, d):
        return ["--db", "x", "--public", "--caps-file", os.path.join(d, "c.json"),
                "--usage-log", os.path.join(d, "u.jsonl")]

    def test_equal_codes_refuse_to_start(self):
        with tempfile.TemporaryDirectory() as d:
            code, err, run = self.run_main(self.args(d), {"NECRO_ACCESS_CODE": JUDGE, "NECRO_JUDGE_CODE": JUDGE})
        self.assertEqual(code, 2)
        self.assertIn("must differ", err)
        self.assertNotIn(JUDGE, err)
        run.assert_not_called()

    def test_short_judge_code_refuses_and_says_the_minimum(self):
        with tempfile.TemporaryDirectory() as d:
            code, err, run = self.run_main(self.args(d), {"NECRO_ACCESS_CODE": CODE, "NECRO_JUDGE_CODE": "short"})
        self.assertEqual(code, 2)
        self.assertIn(str(MIN_JUDGE_CODE_CHARS), err)
        self.assertNotIn("short\n", err)
        run.assert_not_called()

    def test_bad_expiry_and_bad_judge_limit_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"NECRO_ACCESS_CODE": CODE, "NECRO_JUDGE_CODE": JUDGE}
            code, err, _ = self.run_main(self.args(d), {**env, "NECRO_JUDGE_EXPIRES": "2027-01-29"})
            self.assertEqual(code, 2)
            code, err, _ = self.run_main(self.args(d), {**env, "NECRO_JUDGE_MAX_SESSION_SECONDS": "481"})
            self.assertEqual(code, 2)

    def test_both_codes_start_and_wire_the_gate_and_limits(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"NECRO_ACCESS_CODE": CODE, "NECRO_JUDGE_CODE": JUDGE, "NECRO_JUDGE_DAILY_CAP": "7",
                   "NECRO_JUDGE_EXPIRES": "none", "NECRO_DISABLE_PUBLIC": "1",
                   "NECRO_CONTROL_DIR": os.path.join(d, "ctl")}
            with mock.patch("voice.server.build_app", wraps=server.build_app) as ba:
                code, err, run = self.run_main(self.args(d), env)
        self.assertIsNone(code)
        for secret in (CODE, JUDGE):
            self.assertNotIn(secret, err)
        kw = ba.call_args.kwargs
        self.assertEqual(kw["limits"].judge_daily_cap, 7)
        self.assertEqual(kw["limits"].judge_total_cap, 150)
        g = kw["gate"]
        self.assertFalse(g.allowed("public"))
        self.assertTrue(g.allowed("judge"))
        self.assertIsNone(g.judge_expires)

    def test_default_expiry_is_end_of_29_jan_2027_pacific(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("voice.server.build_app", wraps=server.build_app) as ba:
                self.run_main(self.args(d), {"NECRO_ACCESS_CODE": CODE, "NECRO_JUDGE_CODE": JUDGE,
                                             "NECRO_JUDGE_EXPIRES": ""})
        self.assertEqual(ba.call_args.kwargs["gate"].judge_expires.isoformat(), "2027-01-29T23:59:59-08:00")


del MainTest   # only the subclass runs here; the base class has its own module

if __name__ == "__main__":
    unittest.main()
