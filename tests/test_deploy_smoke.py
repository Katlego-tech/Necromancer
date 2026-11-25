"""deploy/smoke_test.py against a local public-mode app (fake Bedrock; nothing billed) and a Host-rewriting proxy."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer

from tests.fake_bedrock import FakeFactory, make_spirit
from voice.guard import AccessGate, CapStore, Limits
from voice.server import SESSION, build_app
from voice.summon import Registry
from voice.usage import UsageLog

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("smoke_test", ROOT / "deploy" / "smoke_test.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

CODE = "correct-horse"


def public_app(tmp, daily_cap=20):
    reg = Registry()
    reg.register(make_spirit("enron"))
    limits = Limits(max_session_seconds=180, idle_seconds=60, daily_cap=daily_cap, total_cap=200)
    return build_app(reg, "enron", FakeFactory(), gate=AccessGate(CODE), limits=limits,
                     caps=CapStore(str(Path(tmp) / "caps.json"), limits),
                     usage_log=UsageLog(str(Path(tmp) / "usage.jsonl")), public=True)


class SmokeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    async def _server(self, **kw):
        server = TestServer(public_app(self.tmp.name, **kw), host="127.0.0.1")
        await server.start_server()
        self.addAsyncCleanup(server.close)
        return server

    async def test_all_checks_pass_against_a_healthy_server(self):
        server = await self._server()
        results = await smoke.run(f"http://127.0.0.1:{server.port}", CODE)
        self.assertEqual([r[0] for r in results],
                         ["ui", "foreign-origin", "browser-upgrade", "wrong-code", "right-code"])
        self.assertTrue(all(ok for _, ok, _ in results), results)
        self.assertIn("ready", results[-1][2])

    async def test_capped_state_is_reported(self):
        server = await self._server()
        caps = server.app[SESSION].caps
        while caps.try_acquire()[0]:
            pass
        results = await smoke.run(f"http://127.0.0.1:{server.port}", CODE)
        self.assertTrue(all(ok for _, ok, _ in results), results)
        self.assertIn("CAP USED UP", dict((n, d) for n, _, d in results)["right-code"])

    async def test_wrong_proxy_host_rewrite_is_detected(self):
        """A proxy that replaces Host with the upstream address breaks the Origin check; the smoke test says so."""
        backend = await self._server()

        async def proxy(request):
            import aiohttp
            headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host",)}
            headers["Host"] = f"127.0.0.1:{backend.port}"          # rewritten Host (Origin keeps the proxy host)
            async with ClientSession() as s:
                try:
                    async with s.ws_connect(f"http://127.0.0.1:{backend.port}{request.path_qs}", headers=headers):
                        return web.Response(status=500)
                except aiohttp.WSServerHandshakeError as e:
                    return web.Response(status=e.status, text="origin not allowed")

        papp = web.Application()
        papp.router.add_get("/events", proxy)

        async def page(request):
            return web.Response(text="<html></html>", content_type="text/html")
        papp.router.add_get("/ui/index.html", page)
        pserver = TestServer(papp, host="127.0.0.1")
        await pserver.start_server()
        self.addAsyncCleanup(pserver.close)
        results = dict((n, (ok, d)) for n, ok, d in await smoke.run(f"http://127.0.0.1:{pserver.port}"))
        self.assertFalse(results["browser-upgrade"][0])
        self.assertIn("Host header", results["browser-upgrade"][1])

    async def test_port_closed_check(self):
        server = await self._server()
        ok = await smoke.run(f"http://127.0.0.1:{server.port}", None, f"127.0.0.1:{server.port}")
        self.assertFalse(dict((n, o) for n, o, _ in ok)["app-port-closed"])   # open port is reported as a failure


if __name__ == "__main__":
    unittest.main()
