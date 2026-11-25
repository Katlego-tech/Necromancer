"""Access code, public mode and controller wiring through the aiohttp server."""
import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from aiohttp import WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from tests import fake_bedrock as fb
from tests.fake_bedrock import FakeFactory, make_spirit
from tests.test_voice_server import read_events
from voice import server
from voice.guard import AccessGate, CapStore, Limits
from voice.server import SESSION, build_app
from voice.summon import Registry
from voice.usage import UsageLog

CODE = "zq-CODE-7f3a91"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


async def start(*scripts, code=CODE, public=False, limits=None, gate=None, caps=None, **kw):
    tmp = tempfile.TemporaryDirectory()
    limits = limits or Limits(disconnect_grace=0.05)
    reg = Registry()
    reg.register(make_spirit("enron"))
    gate = gate if gate is not None else AccessGate(code, delay=0)
    app = build_app(reg, "enron", FakeFactory(*scripts), frame_seconds=0.002, filler_after=30,
                    tool_timeout=60, renew_after=1e9, gate=gate, limits=limits,
                    caps=caps or CapStore(os.path.join(tmp.name, "caps.json"), limits),
                    usage_log=UsageLog(os.path.join(tmp.name, "usage.jsonl")),
                    public=public, **kw)
    client = TestClient(TestServer(app, host="127.0.0.1"))
    await client.start_server()
    client._tmp = tmp
    return client


async def close_code(ws, timeout=3):
    async with asyncio.timeout(timeout):
        while True:
            msg = await ws.receive()
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
                return ws.close_code


async def auth(ws, code=CODE):
    await ws.send_str(json.dumps({"type": "auth", "code": code}))


class AccessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = None

    async def asyncTearDown(self):
        if self.client:
            await self.client.close()
            self.client._tmp.cleanup()

    async def test_missing_code_frame_times_out(self):
        self.client = await start()
        ws = await self.client.ws_connect("/events")
        first = json.loads((await ws.receive()).data)
        self.assertEqual((first["type"], first["reason"], first["can_restart"]), ("paused", "code_required", True))
        async with asyncio.timeout(8):
            got = json.loads((await ws.receive()).data)
            self.assertEqual(got["reason"], "bad_code")
            self.assertEqual(await close_code(ws), 4401)

    async def test_wrong_code_and_text_first_frame(self):
        self.client = await start()
        for send in (lambda ws: auth(ws, "nope"), lambda ws: ws.send_str(json.dumps({"type": "text", "text": "hi"}))):
            ws = await self.client.ws_connect("/io")
            await ws.receive()
            await send(ws)
            self.assertEqual(json.loads((await ws.receive()).data)["reason"], "bad_code")
            self.assertEqual(await close_code(ws), 4401)
        self.assertEqual(self.client.server.app[SESSION].factory.streams, [])

    async def test_correct_code_replay_and_text_round_trip(self):
        self.client = await start([
            fb.on_text("find", fb.completion_start(), fb.assistant_text("Hello.", "FINAL", "END_TURN"),
                       fb.completion_end())])
        ev_ws = await self.client.ws_connect("/events")
        await ev_ws.receive()
        await auth(ev_ws)
        io_ws = await self.client.ws_connect("/io")
        await io_ws.receive()
        await auth(io_ws)
        await io_ws.send_str(json.dumps({"type": "text", "text": "find it"}))
        got = await read_events(ev_ws, lambda e: e["type"] == "transcript" and e["role"] == "spirit")
        self.assertTrue(any(e["type"] == "spirit" for e in got))
        late = await self.client.ws_connect("/events")
        await late.receive()
        await auth(late)
        replay = await read_events(late, lambda e: e["type"] == "transcript" and e["role"] == "spirit")
        self.assertTrue(replay)

    async def test_no_auth_without_gate(self):
        self.client = await start(gate=AccessGate(None))
        io_ws = await self.client.ws_connect("/io")
        await io_ws.send_str(json.dumps({"type": "text", "text": "hi"}))
        async with asyncio.timeout(3):
            while not self.client.server.app[SESSION].factory.streams:
                await asyncio.sleep(0.01)

    async def test_compare_digest_used_with_equal_length_digests(self):
        calls = []
        real = server.AccessGate.check.__globals__["hmac"].compare_digest

        def spy(a, b):
            calls.append((len(a), len(b)))
            return real(a, b)
        gate = AccessGate(CODE, delay=0)
        with mock.patch("voice.guard.hmac.compare_digest", spy):
            self.assertFalse(gate.check("x"))
            self.assertFalse(gate.check("a much longer candidate than the code itself"))
            self.assertTrue(gate.check(CODE))
        self.assertEqual(calls, [(32, 32)] * 3)

    async def test_overlong_candidate_rejected_without_comparing(self):
        gate = AccessGate(CODE, delay=0)
        with mock.patch("voice.guard.hmac.compare_digest") as cd:
            self.assertFalse(gate.check("a" * 257))
            self.assertFalse(gate.check(None))
        cd.assert_not_called()
        self.assertNotIn(CODE, repr(vars(gate)))

    async def test_lockout_after_five_failures_and_window(self):
        clock = Clock()
        gate = AccessGate(CODE, delay=0, clock=clock)
        for _ in range(5):
            self.assertEqual(await gate.attempt("r", "bad"), "bad")
        self.assertEqual(await gate.attempt("r", CODE), "locked")
        self.assertEqual(await gate.attempt("other", CODE), "ok")
        clock.t = 61
        self.assertEqual(await gate.attempt("r", CODE), "ok")

    async def test_global_limiter_and_delay(self):
        gate = AccessGate(CODE, delay=0, global_limit=3, clock=Clock())
        for i in range(3):
            self.assertEqual(await gate.attempt(f"r{i}", "bad"), "bad")
        self.assertEqual(await gate.attempt("fresh", CODE), "locked")
        slept = []
        real_sleep = asyncio.sleep

        async def fake_sleep(d, *a):
            slept.append(d)
            await real_sleep(0)
        slow = AccessGate(CODE, delay=1.0)
        with mock.patch("voice.guard.asyncio.sleep", fake_sleep):
            await slow.attempt("r", "bad")
            await slow.attempt("r", CODE)
        self.assertEqual(slept, [1.0])

    async def _lock_out(self, xff):
        for _ in range(5):
            ws = await self.client.ws_connect("/io", headers={"X-Forwarded-For": xff})
            await ws.receive()
            await auth(ws, "bad")
            await close_code(ws)
    async def _try(self, xff):
        ws = await self.client.ws_connect("/io", headers={"X-Forwarded-For": xff})
        await ws.receive()
        await auth(ws)
        try:  # a correct code gets no reply on /io; a rejected one gets a paused event at once
            msg = await asyncio.wait_for(ws.receive(), 1.0)
        except asyncio.TimeoutError:
            await ws.close()
            return {"accepted": True}
        await ws.close()
        return json.loads(msg.data) if msg.type == WSMsgType.TEXT else {"closed": ws.close_code}
    async def test_behind_trusted_proxy_one_visitors_bad_codes_do_not_lock_out_another(self):
        self.client = await start(gate=AccessGate(CODE, delay=0, clock=Clock()), trust_proxy=True)
        await self._lock_out("203.0.113.5")
        msg = await self._try("198.51.100.7")          # a different visitor (e.g. a judge)
        self.assertEqual(msg, {"accepted": True})
        locked = await self._try("203.0.113.5")        # the guesser is still locked out
        self.assertEqual(locked["reason"], "bad_code")
        self.assertIn("try later", locked["message"])
    async def test_without_trust_proxy_a_forwarded_header_cannot_dodge_the_lockout(self):
        self.client = await start(gate=AccessGate(CODE, delay=0, clock=Clock()))
        await self._lock_out("203.0.113.5")
        msg = await self._try("198.51.100.7")          # header ignored: same peer, still locked
        self.assertEqual(msg["reason"], "bad_code")
        self.assertIn("try later", msg["message"])
    async def test_locked_over_websocket_closes_4429(self):
        gate = AccessGate(CODE, delay=0, clock=Clock())
        self.client = await start(gate=gate)
        for _ in range(5):
            ws = await self.client.ws_connect("/io")
            await ws.receive()
            await auth(ws, "bad")
            await close_code(ws)
        ws = await self.client.ws_connect("/io")
        await ws.receive()
        await auth(ws)
        msg = json.loads((await ws.receive()).data)
        self.assertEqual(msg["reason"], "bad_code")
        self.assertIn("try later", msg["message"])
        self.assertEqual(await close_code(ws), 4429)

    async def test_origin_check_precedes_auth(self):
        self.client = await start()
        for path in ("/events", "/io"):
            with self.assertRaises(WSServerHandshakeError) as cm:
                await self.client.ws_connect(path, headers={"Origin": "http://evil.example"})
            self.assertEqual(cm.exception.status, 403)

    async def test_public_mode_waits_for_start(self):
        self.client = await start(public=True)
        io_ws = await self.client.ws_connect("/io")
        await io_ws.receive()
        await auth(io_ws)
        await asyncio.sleep(0.1)
        factory = self.client.server.app[SESSION].factory
        self.assertEqual(factory.streams, [])
        await io_ws.send_str(json.dumps({"type": "start"}))
        async with asyncio.timeout(3):
            while not factory.streams:
                await asyncio.sleep(0.01)

    async def test_local_mode_connect_starts_one_stream_for_two_simultaneous(self):
        self.client = await start(gate=AccessGate(None))
        a, b = await asyncio.gather(self.client.ws_connect("/io"), self.client.ws_connect("/io"))
        factory = self.client.server.app[SESSION].factory
        async with asyncio.timeout(3):
            while not factory.streams:
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        self.assertEqual(len(factory.streams), 1)
        self.assertEqual(self.client.server.app[SESSION].caps.snapshot()["daily_remaining"], 19)
        await a.close()
        await b.close()

    async def test_last_disconnect_closes_stream_after_grace(self):
        self.client = await start(gate=AccessGate(None), limits=Limits(disconnect_grace=0.1))
        ctl = self.client.server.app[SESSION]
        a = await self.client.ws_connect("/io")
        b = await self.client.ws_connect("/io")
        async with asyncio.timeout(3):
            while not ctl.factory.streams:
                await asyncio.sleep(0.01)
        await a.close()
        await asyncio.sleep(0.3)
        self.assertEqual(ctl.state, "live")  # b is still connected
        self.assertEqual(ctl.caps.snapshot()["daily_remaining"], 19)  # joiner did not recount
        await b.close()
        async with asyncio.timeout(3):
            while ctl.state != "off":
                await asyncio.sleep(0.01)
        self.assertTrue(ctl.factory.streams[0].closed)

    async def test_cap_hit_emits_paused_and_text_does_not_raise(self):
        limits = Limits(daily_cap=0)
        self.client = await start(gate=AccessGate(None), limits=limits)
        ev_ws = await self.client.ws_connect("/events")
        io_ws = await self.client.ws_connect("/io")
        got = await read_events(ev_ws, lambda e: e["type"] == "paused")
        self.assertEqual(got[-1]["reason"], "daily_cap")
        await io_ws.send_str(json.dumps({"type": "text", "text": "hello"}))
        got = await read_events(ev_ws, lambda e: e["type"] == "paused")
        self.assertEqual(got[-1]["reason"], "daily_cap")
        self.assertEqual(self.client.server.app[SESSION].factory.streams, [])


class MainTest(unittest.TestCase):
    def run_main(self, argv, env):
        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stderr(err), \
                mock.patch("voice.server.web.run_app") as run, \
                mock.patch("voice.server.importlib.import_module") as im, \
                mock.patch("voice.stream.BedrockFactory"):
            im.return_value.make_spirit.return_value = make_spirit("enron")
            try:
                server.main(argv)
                code = None
            except SystemExit as e:
                code = e.code
        return code, err.getvalue(), run

    def test_public_without_code_exits_2(self):
        env = {"NECRO_ACCESS_CODE": "  "}
        code, err, run = self.run_main(["--db", "x", "--public"], env)
        self.assertEqual(code, 2)
        self.assertIn("NECRO_ACCESS_CODE is not set", err)
        run.assert_not_called()

    def test_public_with_code_starts(self):
        with tempfile.TemporaryDirectory() as d:
            code, err, run = self.run_main(
                ["--db", "x", "--public", "--caps-file", os.path.join(d, "c.json"),
                 "--usage-log", os.path.join(d, "u.jsonl")], {"NECRO_ACCESS_CODE": CODE})
        self.assertIsNone(code)
        self.assertNotIn(CODE, err)
        run.assert_called_once()

    def test_bad_limit_is_argparse_error(self):
        code, err, run = self.run_main(["--db", "x", "--max-session-seconds", "481"], {})
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_env_used_when_flag_absent(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, run = self.run_main(
                ["--db", "x", "--caps-file", os.path.join(d, "c.json")],
                {"NECRO_MAX_SESSION_SECONDS": "999", "NECRO_ACCESS_CODE": ""})
        self.assertEqual(code, 2)
        with tempfile.TemporaryDirectory() as d:
            code, _, run = self.run_main(
                ["--db", "x", "--max-session-seconds", "60", "--caps-file", os.path.join(d, "c.json"),
                 "--usage-log", os.path.join(d, "u.jsonl")],
                {"NECRO_MAX_SESSION_SECONDS": "999", "NECRO_ACCESS_CODE": ""})
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
