"""Local aiohttp server: /events (UI protocol), /io (audio and text), static UI.

Binds 127.0.0.1 only. WebSocket upgrades whose Origin host differs from the Host header
are refused. Only ui/ and the mascot assets are served, never the repo root.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from voice.emitter import Emitter
from voice.guard import AccessGate, CapStore, Limits
from voice.live import LiveController
from voice.session import VoiceSession
from voice.summon import Registry
from voice.usage import UsageLog

ROOT = Path(__file__).resolve().parent.parent
SESSION = web.AppKey("session", LiveController)
GATE = web.AppKey("gate", AccessGate)
PUBLIC = web.AppKey("public", bool)
EMITTER = web.AppKey("emitter", Emitter)
MAX_MSG = 1 << 20
SPIRIT_MODULES = {"enron": "spirits.enron.spirit", "wayback": "spirits.wayback.spirit"}
def load_registry(start: str, db: str | None) -> Registry:
    """Register every loadable spirit (enron only with a db); the engine imports none statically."""
    registry = Registry()
    for sid, mod in SPIRIT_MODULES.items():
        if sid == "enron" and not db:
            continue
        module = importlib.import_module(mod)
        registry.register(module.make_spirit(db) if sid == "enron" else module.make_spirit())
    if start not in registry:
        raise ValueError(f"spirit {start} is not loadable (enron needs --db)")
    return registry


def _origin_ok(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc == request.headers.get("Host", "")


async def _authenticate(request: web.Request, ws: web.WebSocketResponse) -> bool:
    gate: AccessGate = request.app[GATE]
    if not gate.required:
        return True
    ctl: LiveController = request.app[SESSION]
    try:
        await ws.send_str(json.dumps(ctl.paused_event("code_required")))
    except ConnectionResetError:
        return False
    code = None
    try:
        msg = await ws.receive(timeout=5)
        if msg.type == WSMsgType.TEXT and len(msg.data) <= 512:
            data = json.loads(msg.data)
            if isinstance(data, dict) and data.get("type") == "auth":
                code = data.get("code")
    except (asyncio.TimeoutError, ValueError):
        pass
    verdict = await gate.attempt(request.remote or "?", code)
    if verdict == "ok":
        return True
    ev = ctl.paused_event("bad_code")
    if verdict == "locked":
        ev["message"] = "Too many attempts, try later."
    try:
        await ws.send_str(json.dumps(ev))
        await ws.close(code=4429 if verdict == "locked" else 4401)
    except ConnectionResetError:
        pass
    return False


async def events_ws(request: web.Request) -> web.StreamResponse:
    if not _origin_ok(request):
        return web.Response(status=403, text="origin not allowed")
    ws = web.WebSocketResponse(max_msg_size=MAX_MSG)
    await ws.prepare(request)
    if not await _authenticate(request, ws):
        return ws
    emitter: Emitter = request.app[EMITTER]
    queue: asyncio.Queue = asyncio.Queue()
    for ev in emitter.events:  # late joiners see the history so far
        queue.put_nowait(ev)
    status = request.app[SESSION].status_event()
    if status is not None:
        queue.put_nowait(status)
    sink = queue.put_nowait
    emitter.add_sink(sink)

    async def writer():
        while True:
            ev = await queue.get()
            await ws.send_str(json.dumps(ev))

    task = asyncio.create_task(writer())
    try:
        async for _ in ws:  # server to browser only; ignore anything received
            pass
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if sink in emitter.sinks:
            emitter.sinks.remove(sink)
    return ws


async def io_ws(request: web.Request) -> web.StreamResponse:
    if not _origin_ok(request):
        return web.Response(status=403, text="origin not allowed")
    ws = web.WebSocketResponse(max_msg_size=MAX_MSG)
    await ws.prepare(request)
    if not await _authenticate(request, ws):
        return ws
    session: LiveController = request.app[SESSION]
    session.client_joined()
    emitter: Emitter = request.app[EMITTER]
    out: asyncio.Queue = asyncio.Queue()
    audio_sink = lambda pcm: out.put_nowait(pcm)  # noqa: E731
    clear_sink = lambda: out.put_nowait("clear")  # noqa: E731
    session.audio_sinks.append(audio_sink)
    session.clear_sinks.append(clear_sink)

    async def writer():
        while True:
            item = await out.get()
            if item == "clear":
                await ws.send_str(json.dumps({"type": "clear"}))
            else:
                await ws.send_bytes(item)

    task = asyncio.create_task(writer())
    jobs: set[asyncio.Task] = set()

    def spawn(coro):
        t = asyncio.create_task(coro)
        jobs.add(t)
        t.add_done_callback(jobs.discard)

    try:
        if not request.app[PUBLIC]:
            await session.ensure_started()
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                session.feed_audio(msg.data)
            elif msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    kind = data.get("type")
                    if kind == "text" and isinstance(data.get("text"), str) and data["text"].strip():
                        spawn(session.send_text(data["text"]))
                    elif kind == "start":
                        spawn(session.ensure_started())
                    elif kind == "summon" and isinstance(data.get("spirit"), str):
                        spawn(session.summon(data["spirit"]))
                    else:
                        emitter.error("ignored malformed /io message")
                except (ValueError, AttributeError):
                    emitter.error("ignored malformed /io message")
    finally:
        for j in list(jobs):
            j.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        session.client_left()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for sinks, sink in ((session.audio_sinks, audio_sink), (session.clear_sinks, clear_sink)):
            if sink in sinks:
                sinks.remove(sink)
    return ws


async def index(request: web.Request) -> web.StreamResponse:
    raise web.HTTPFound("/ui/index.html")


def build_app(registry: Registry, spirit_id: str, factory, *, record_path: str | None = None,
              gate: AccessGate | None = None, limits: Limits | None = None, caps: CapStore | None = None,
              usage_log: UsageLog | None = None, video_url: str | None = None, public: bool = False,
              **session_kwargs) -> web.Application:
    emitter = Emitter(record_path)
    limits = limits or Limits()
    if caps is None:
        caps = CapStore(os.path.join(tempfile.mkdtemp(prefix="necro-caps-"), "caps.json"), limits)
    if usage_log is None:
        usage_log = UsageLog(os.path.join(tempfile.mkdtemp(prefix="necro-usage-"), "usage.jsonl"))
    session = LiveController(
        lambda: VoiceSession(registry, spirit_id, factory, emitter, **session_kwargs),
        emitter, limits, caps, usage_log, factory=factory, video_url=video_url,
        autostart_on_connect=not public)
    app = web.Application()
    app[SESSION] = session
    app[GATE] = gate or AccessGate(None)
    app[PUBLIC] = public
    app[EMITTER] = emitter
    app.router.add_get("/", index)
    app.router.add_get("/events", events_ws)
    app.router.add_get("/io", io_ws)
    app.router.add_static("/ui", ROOT / "ui")
    app.router.add_static("/mascot/assets", ROOT / "mascot" / "assets")
    for name in ("mascot.js", "mascot.css"):
        app.router.add_get("/mascot/" + name, _file_handler(ROOT / "mascot" / name))

    async def on_cleanup(_app):
        await session.shutdown()
        emitter.close()

    app.on_cleanup.append(on_cleanup)
    return app


def _file_handler(path: Path):
    async def handler(request: web.Request) -> web.StreamResponse:
        return web.FileResponse(path)
    return handler


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Necromancer voice server (127.0.0.1 only)")
    ap.add_argument("--spirit", default="enron", choices=sorted(SPIRIT_MODULES))
    ap.add_argument("--db", help="path to the Enron SQLite index (required for the enron spirit)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--record", help="write emitted UI events to this JSONL file")
    ap.add_argument("--public", action="store_true",
                    help="require NECRO_ACCESS_CODE and start live voice only on an explicit start")
    opts = (("max_session_seconds", "NECRO_MAX_SESSION_SECONDS", float, 180),
            ("idle_seconds", "NECRO_IDLE_SECONDS", float, 60),
            ("daily_cap", "NECRO_DAILY_CAP", int, 20),
            ("total_cap", "NECRO_TOTAL_CAP", int, 200),
            ("usage_log", "NECRO_USAGE_LOG", str, "var/usage.jsonl"),
            ("caps_file", "NECRO_CAPS_FILE", str, "var/caps.json"),
            ("demo_video_url", "NECRO_DEMO_VIDEO_URL", str, None))
    for flag, env, typ, default in opts:
        ap.add_argument("--" + flag.replace("_", "-"), type=typ, default=None,
                        help=f"env {env}, default {default}")
    args = ap.parse_args(argv)
    for flag, env, typ, default in opts:
        if getattr(args, flag) is None:
            raw = os.environ.get(env, "")
            try:
                setattr(args, flag, typ(raw) if raw.strip() else default)
            except ValueError:
                ap.error(f"{env} is not valid")
    code = os.environ.get("NECRO_ACCESS_CODE", "")
    if args.public and not code.strip():
        print("NECRO_ACCESS_CODE is not set; refusing to start in public mode", file=sys.stderr)
        sys.exit(2)
    try:
        limits = Limits(max_session_seconds=args.max_session_seconds, idle_seconds=args.idle_seconds,
                        daily_cap=args.daily_cap, total_cap=args.total_cap)
    except ValueError as e:
        ap.error(str(e))
    gate = AccessGate(code if code.strip() else None)

    from voice.stream import BedrockFactory

    # Spirits are loaded by name from their data module; the engine imports none statically.
    try:
        registry = load_registry(args.spirit, args.db)
    except ValueError as e:
        ap.error(str(e))
    app = build_app(registry, args.spirit, BedrockFactory(), record_path=args.record,
                    gate=gate, limits=limits, caps=CapStore(args.caps_file, limits),
                    usage_log=UsageLog(args.usage_log), video_url=args.demo_video_url,
                    public=args.public, turn_grace=12.0)
    print(f"Open http://127.0.0.1:{args.port}/ui/index.html"
          f"?ws=ws://127.0.0.1:{args.port}/events&io=ws://127.0.0.1:{args.port}/io")
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
