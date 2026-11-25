"""POST /alert and the /io alert message."""
import asyncio
import json
import os
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

from tests.fake_bedrock import FakeFactory, make_spirit, wait_for
from tests.test_grid_alert import SPOKEN, registry, speak
from tests.test_voice_server import read_events
from voice.guard import AccessGate, CapStore, Limits
from voice.protocol_check import validate_event
from voice.server import EMITTER, SESSION, build_app
from voice.summon import Registry
from voice.usage import UsageLog

CODE = "zq-CODE-7f3a91"


async def client_for(*scripts, code=None, reg=None, public=False, factory=None):
    tmp = tempfile.TemporaryDirectory()
    limits = Limits(disconnect_grace=0.05)
    app = build_app(reg or registry(), "enron", factory or FakeFactory(*scripts), frame_seconds=0.002,
                    filler_after=30, tool_timeout=60, renew_after=1e9, public=public, limits=limits,
                    gate=AccessGate(code, delay=0) if code else None,
                    caps=CapStore(os.path.join(tmp.name, "caps.json"), limits),
                    usage_log=UsageLog(os.path.join(tmp.name, "usage.jsonl")))
    client = TestClient(TestServer(app, host="127.0.0.1"))
    await client.start_server()
    client._tmp = tmp
    return client


class GridServerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = None

    async def asyncTearDown(self):
        if self.client:
            await self.client.close()
            self.client._tmp.cleanup()

    async def test_post_alert_live_speaks_and_events_are_valid(self):
        self.client = await client_for([], [speak()], [])
        ev_ws = await self.client.ws_connect("/events")
        io_ws = await self.client.ws_connect("/io")
        await wait_for(lambda: self.client.server.app[SESSION].state == "live")
        r = await self.client.post("/alert", json={}, headers={"Origin": f"http://127.0.0.1:{self.client.port}"})
        self.assertEqual((r.status, (await r.json())["status"]), (202, "started"))
        got = await read_events(ev_ws, lambda e: e["type"] == "transcript" and e["role"] == "spirit"
                                and e["final"] and e["text"] == SPOKEN)
        for ev in got:
            self.assertIsNone(validate_event(ev), ev)
        ev = [e for e in got if e["type"] == "evidence"][0]
        self.assertEqual(ev["metadata"]["mode"], "replay")
        await io_ws.close()

    async def test_foreign_origin_403_and_nothing_started(self):
        self.client = await client_for()
        r = await self.client.post("/alert", json={}, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)
        self.assertEqual(self.client.server.app[EMITTER].events, [])

    async def test_gate_missing_bad_good_and_lock(self):
        self.client = await client_for(code=CODE, public=True)
        self.assertEqual((await self.client.post("/alert", json={})).status, 401)
        r = await self.client.post("/alert", json={}, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(r.status, 401)
        r = await self.client.post("/alert", json={}, headers={"Authorization": f"Bearer {CODE}"})
        self.assertEqual((r.status, (await r.json())["status"]), (202, "visual"))
        r = await self.client.post("/alert", json={}, headers={"X-Necro-Code": CODE})
        self.assertEqual(r.status, 202)
        for _ in range(6):
            r = await self.client.post("/alert", json={}, headers={"X-Necro-Code": "bad"})
        self.assertEqual(r.status, 429)
        r = await self.client.post("/alert", json={}, headers={"X-Necro-Code": CODE})
        self.assertEqual(r.status, 429)

    async def test_oversize_body_413_and_bad_json_400(self):
        self.client = await client_for(public=True)
        r = await self.client.post("/alert", data=b'{"area":"' + b"x" * 2000 + b'"}')
        self.assertEqual(r.status, 413)
        r = await self.client.post("/alert", data=b"{nope")
        self.assertEqual(r.status, 400)

    async def test_cold_area_404_with_cold_evidence(self):
        self.client = await client_for(public=True)
        r = await self.client.post("/alert", json={"area": "Atlantis"})
        self.assertEqual((r.status, (await r.json())["status"]), (404, "cold"))
        events = self.client.server.app[EMITTER].events
        self.assertEqual([e["type"] for e in events], ["evidence", "transcript"])
        self.assertIsNone(events[0]["source"])
        self.assertEqual(events[1]["text"], "The trail goes cold.")
        for ev in events:
            self.assertIsNone(validate_event(ev), ev)

    async def test_no_live_session_is_visual_only(self):
        self.client = await client_for(public=True)
        r = await self.client.post("/alert", json={})
        self.assertEqual((r.status, (await r.json())["status"]), (202, "visual"))
        ctl = self.client.server.app[SESSION]
        self.assertEqual((ctl.state, ctl.session), ("off", None))
        self.assertNotIn("state", [e["type"] for e in self.client.server.app[EMITTER].events])

    async def test_grid_not_registered_503(self):
        reg = Registry()
        reg.register(make_spirit("enron"))
        self.client = await client_for(reg=reg, public=True)
        self.assertEqual((await self.client.post("/alert", json={})).status, 503)

    async def test_io_alert_message_on_authenticated_socket(self):
        self.client = await client_for([], [speak()], [], code=CODE)
        ev_ws = await self.client.ws_connect("/events")
        await ev_ws.receive()
        await ev_ws.send_str(json.dumps({"type": "auth", "code": CODE}))
        io_ws = await self.client.ws_connect("/io")
        await io_ws.receive()
        await io_ws.send_str(json.dumps({"type": "alert"}))   # not authenticated: consumed as the auth attempt
        await asyncio.sleep(0.1)
        self.assertEqual(self.client.server.app[EMITTER].events and
                         [e for e in self.client.server.app[EMITTER].events if e["type"] == "evidence"], [])
        io2 = await self.client.ws_connect("/io")
        await io2.receive()
        await io2.send_str(json.dumps({"type": "auth", "code": CODE}))
        await wait_for(lambda: self.client.server.app[SESSION].state == "live")
        await io2.send_str(json.dumps({"type": "alert"}))
        emitter = self.client.server.app[EMITTER]
        await wait_for(lambda: any(e["type"] == "transcript" and e["text"] == SPOKEN for e in emitter.events))
        await io2.close()


if __name__ == "__main__":
    unittest.main()
