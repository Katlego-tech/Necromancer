"""aiohttp server against the fake Bedrock factory."""
import asyncio
import json
import unittest

from aiohttp import WSMsgType, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from tests import fake_bedrock as fb
from tests.fake_bedrock import FakeFactory, make_spirit
from voice.protocol_check import validate_event
from voice.server import build_app
from voice.summon import Registry


async def client_for(*scripts):
    reg = Registry()
    reg.register(make_spirit("enron"))
    app = build_app(reg, "enron", FakeFactory(*scripts), frame_seconds=0.002, filler_after=30,
                    tool_timeout=60, renew_after=1e9)
    client = TestClient(TestServer(app, host="127.0.0.1"))
    await client.start_server()
    return client


async def read_events(ws, until, timeout=3.0):
    got = []
    async with asyncio.timeout(timeout):
        while True:
            msg = await ws.receive()
            if msg.type != WSMsgType.TEXT:
                raise AssertionError(f"unexpected frame {msg.type}")
            ev = json.loads(msg.data)
            got.append(ev)
            if until(ev):
                return got


class VoiceServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = None

    async def asyncTearDown(self):
        if self.client:
            await self.client.close()

    async def test_events_frames_valid_and_text_yields_events(self):
        self.client = await client_for([
            fb.on_text("find", fb.completion_start(), fb.tool_use("lookup", "tu-1", {"query": "x"}),
                       fb.assistant_text("Searching.", "FINAL", "END_TURN"), fb.completion_end())])
        ev_ws = await self.client.ws_connect("/events")
        io_ws = await self.client.ws_connect("/io")
        await io_ws.send_str(json.dumps({"type": "text", "text": "find it"}))
        got = await read_events(ev_ws, lambda e: e["type"] == "evidence")
        for ev in got:
            self.assertIsNone(validate_event(ev), ev)
        types = [e["type"] for e in got]
        for t in ("spirit", "appearance", "state", "transcript", "evidence"):
            self.assertIn(t, types)
        ev = got[-1]
        self.assertEqual(ev["source"], "ev-0001")
        self.assertEqual(ev["content"], fb.EMAIL_RESULT["content"])
        user = [e for e in got if e["type"] == "transcript" and e["role"] == "user"]
        self.assertEqual(user[0]["text"], "find it")

    async def test_interrupt_sends_clear_after_audio(self):
        self.client = await client_for([
            fb.on_text("hi", fb.completion_start(), fb.audio_out(b"\x05\x06" * 4), fb.interrupted())])
        io_ws = await self.client.ws_connect("/io")
        await io_ws.send_str(json.dumps({"type": "text", "text": "hi there"}))
        frames = []
        async with asyncio.timeout(3):
            while not any(k == "clear" for k, _ in frames):
                msg = await io_ws.receive()
                if msg.type == WSMsgType.BINARY:
                    frames.append(("audio", msg.data))
                elif msg.type == WSMsgType.TEXT:
                    frames.append((json.loads(msg.data)["type"], None))
        self.assertEqual(frames[0], ("audio", b"\x05\x06" * 4))
        self.assertEqual(frames[-1][0], "clear")

    async def test_mic_binary_reaches_stream(self):
        self.client = await client_for()
        io_ws = await self.client.ws_connect("/io")
        pcm = b"\x07\x00" * 1024
        await io_ws.send_bytes(pcm)
        async with asyncio.timeout(3):
            while not (self._streams() and pcm in self._streams()[0].audio_payloads()):
                await asyncio.sleep(0.01)

    def _streams(self):
        from voice.server import SESSION
        return self.client.server.app[SESSION].factory.streams

    async def test_static_ui_served_and_repo_root_not(self):
        self.client = await client_for()
        r = await self.client.get("/ui/index.html")
        self.assertEqual(r.status, 200)
        self.assertIn("Necromancer", await r.text())
        for path in ("/mascot/mascot.js", "/mascot/mascot.css"):
            self.assertEqual((await self.client.get(path)).status, 200, path)
        for path in ("/.env", "/data/enron/enron.sqlite", "/voice/server.py", "/README.md"):
            self.assertEqual((await self.client.get(path)).status, 404, path)
        r = await self.client.get("/", allow_redirects=False)
        self.assertEqual((r.status, r.headers["Location"]), (302, "/ui/index.html"))

    async def test_foreign_origin_rejected(self):
        self.client = await client_for()
        for path in ("/events", "/io"):
            with self.assertRaises(WSServerHandshakeError) as cm:
                await self.client.ws_connect(path, headers={"Origin": "http://evil.example"})
            self.assertEqual(cm.exception.status, 403)
        host = f"127.0.0.1:{self.client.port}"
        ws = await self.client.ws_connect("/events", headers={"Origin": f"http://{host}"})
        await ws.close()

    async def test_malformed_io_message_reports_error(self):
        self.client = await client_for()
        ev_ws = await self.client.ws_connect("/events")
        io_ws = await self.client.ws_connect("/io")
        await io_ws.send_str("not json")
        got = await read_events(ev_ws, lambda e: e["type"] == "error")
        self.assertIn("malformed", got[-1]["message"])


if __name__ == "__main__":
    unittest.main()
