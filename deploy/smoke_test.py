#!/usr/bin/env python3
"""Smoke test for a deployed Necromancer server. Makes NO Bedrock calls: it never opens /io and
never sends a "start" message, so no billed voice session can begin.

  .venv/bin/python deploy/smoke_test.py https://necro.example.com
  NECRO_SMOKE_CODE=... .venv/bin/python deploy/smoke_test.py https://necro.example.com \\
        --app-port-closed 203.0.113.10:8765

Checks (each prints PASS/FAIL, exit status 1 if any FAIL):
  1. the UI page loads (health)
  2. a WebSocket upgrade with a foreign Origin is refused (403)
  3. a browser-like upgrade (Origin = the site itself) is accepted and asks for the access code
     (this fails if the proxy does not forward the Host header unchanged)
  4. a wrong access code is refused (paused/bad_code, close 4401)
  5. with NECRO_SMOKE_CODE set: the right code is accepted and the paused/capped state is shown
     (reason not_started = ready, daily_cap or total_cap = caps used up). The code is read from
     the environment, never from argv, so it stays out of shell history and `ps`.
  6. with --app-port-closed HOST:PORT: that TCP port does not accept connections from here.
Note: the server counts a wrong code toward its lockout (5 per minute per address, 50 global),
so the whole run adds one failed attempt. Behind a proxy all visitors share one address.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import socket
import sys
from urllib.parse import urlsplit

import aiohttp

WRONG_CODE = "smoke-test-wrong-code"


async def _first_event(ws, timeout=8.0):
    msg = await asyncio.wait_for(ws.receive(), timeout)
    if msg.type != aiohttp.WSMsgType.TEXT:
        raise AssertionError(f"expected a text frame, got {msg.type.name}")
    return json.loads(msg.data)


async def _check_ui(session, base):
    async with session.get(base + "/ui/index.html") as r:
        body = await r.text()
        assert r.status == 200, f"HTTP {r.status}"
        assert "<html" in body.lower(), "response is not an HTML page"
    return "UI page loads"


async def _check_foreign_origin(session, base):
    try:
        async with session.ws_connect(base + "/events", headers={"Origin": "https://evil.example"}):
            raise AssertionError("upgrade with a foreign Origin was accepted")
    except aiohttp.WSServerHandshakeError as e:
        assert e.status == 403, f"expected 403, got {e.status}"
    return "foreign Origin refused with 403"


async def _check_browser_like_upgrade(session, base):
    origin = _origin(base)
    try:
        async with session.ws_connect(base + "/events", headers={"Origin": origin}) as ws:
            ev = await _first_event(ws)
    except aiohttp.WSServerHandshakeError as e:
        raise AssertionError(f"upgrade refused with {e.status}; the proxy must forward the Host header "
                             "unchanged and pass WebSocket upgrades") from None
    assert ev.get("type") == "paused" and ev.get("reason") == "code_required", \
        f"expected paused/code_required first, got {ev.get('type')}/{ev.get('reason')}"
    return "browser-like upgrade accepted and a code is required"


async def _check_wrong_code(session, base):
    async with session.ws_connect(base + "/events", headers={"Origin": _origin(base)}) as ws:
        await _first_event(ws)
        await ws.send_str(json.dumps({"type": "auth", "code": WRONG_CODE}))
        ev = await _first_event(ws)
        assert ev.get("reason") == "bad_code", f"expected bad_code, got {ev.get('reason')}"
        msg = await asyncio.wait_for(ws.receive(), 5)
        assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 4401, \
            f"expected close 4401, got {msg.type.name} {msg.data}"
    return "wrong code refused (bad_code, close 4401)"


async def _check_right_code(session, base, code):
    async with session.ws_connect(base + "/events", headers={"Origin": _origin(base)}) as ws:
        await _first_event(ws)
        await ws.send_str(json.dumps({"type": "auth", "code": code}))
        seen = []
        for _ in range(12):  # history events can come first; stop at the first paused event
            try:
                ev = await _first_event(ws, timeout=3.0)
            except (asyncio.TimeoutError, AssertionError):
                break
            seen.append(ev)
            if ev.get("type") == "paused":
                break
        paused = [e for e in seen if e.get("type") == "paused"]
        assert paused, "no paused event after login: live voice may already be running"
        ev = paused[-1]
        assert ev["reason"] != "bad_code", "the access code was not accepted"
    state = {"not_started": "ready (voice not started)", "daily_cap": "DAILY CAP USED UP",
             "total_cap": "TOTAL CAP USED UP"}.get(ev["reason"], ev["reason"])
    return f"right code accepted; paused state: {state}"


def _check_port_closed(target: str):
    host, _, port = target.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=4):
            raise AssertionError(f"{target} accepts connections: close it in the security group")
    except (ConnectionRefusedError, socket.timeout, TimeoutError, OSError) as e:
        if isinstance(e, AssertionError):
            raise
    return f"{target} does not accept connections"


def _origin(base: str) -> str:
    u = urlsplit(base)
    return f"{u.scheme}://{u.netloc}"


async def run(base: str, code: str | None = None, app_port_closed: str | None = None) -> list[tuple[str, bool, str]]:
    base = base.rstrip("/")
    results: list[tuple[str, bool, str]] = []

    async def step(name, coro):
        try:
            results.append((name, True, await coro))
        except Exception as e:  # noqa: BLE001 - report every failure, keep going
            results.append((name, False, f"{type(e).__name__}: {e}"))

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await step("ui", _check_ui(session, base))
        await step("foreign-origin", _check_foreign_origin(session, base))
        await step("browser-upgrade", _check_browser_like_upgrade(session, base))
        await step("wrong-code", _check_wrong_code(session, base))
        if code:
            await step("right-code", _check_right_code(session, base, code))
    if app_port_closed:
        try:
            results.append(("app-port-closed", True, _check_port_closed(app_port_closed)))
        except Exception as e:  # noqa: BLE001
            results.append(("app-port-closed", False, str(e)))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Smoke test for a deployed Necromancer server (no billed calls)")
    ap.add_argument("base_url", help="e.g. https://necro.example.com")
    ap.add_argument("--app-port-closed", metavar="HOST:PORT",
                    help="also check that the app port is not reachable directly from here")
    args = ap.parse_args(argv)
    if not args.base_url.startswith(("https://", "http://")):
        ap.error("base_url must start with https:// or http://")
    results = asyncio.run(run(args.base_url, os.environ.get("NECRO_SMOKE_CODE") or None, args.app_port_closed))
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not os.environ.get("NECRO_SMOKE_CODE"):
        print("SKIP  right-code: set NECRO_SMOKE_CODE to also check login and the caps/paused state")
    if not args.base_url.startswith("https://"):
        print("NOTE  the URL is not https: browsers will not allow the microphone on a public http site")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
