"""Local aiohttp server: /events (UI protocol), /io (audio and text), static UI.

Binds 127.0.0.1 only. WebSocket upgrades whose Origin host differs from the Host header
are refused. Only ui/ and the mascot assets are served, never the repo root.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from voice.emitter import Emitter
from voice.session import VoiceSession
from voice.summon import Registry

ROOT = Path(__file__).resolve().parent.parent
SESSION = web.AppKey("session", VoiceSession)
EMITTER = web.AppKey("emitter", Emitter)
MAX_MSG = 1 << 20


def _origin_ok(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc == request.headers.get("Host", "")


async def events_ws(request: web.Request) -> web.StreamResponse:
    if not _origin_ok(request):
        return web.Response(status=403, text="origin not allowed")
    ws = web.WebSocketResponse(max_msg_size=MAX_MSG)
    await ws.prepare(request)
    emitter: Emitter = request.app[EMITTER]
    queue: asyncio.Queue = asyncio.Queue()
    for ev in emitter.events:  # late joiners see the history so far
        queue.put_nowait(ev)
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
    session: VoiceSession = request.app[SESSION]
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
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                session.feed_audio(msg.data)
            elif msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    kind = data.get("type")
                    if kind == "text" and isinstance(data.get("text"), str) and data["text"].strip():
                        spawn(session.send_text(data["text"]))
                    elif kind == "summon" and isinstance(data.get("spirit"), str):
                        spawn(session.summon(data["spirit"]))
                    else:
                        emitter.error("ignored malformed /io message")
                except (ValueError, AttributeError):
                    emitter.error("ignored malformed /io message")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for sinks, sink in ((session.audio_sinks, audio_sink), (session.clear_sinks, clear_sink)):
            if sink in sinks:
                sinks.remove(sink)
    return ws


async def index(request: web.Request) -> web.StreamResponse:
    raise web.HTTPFound("/ui/index.html")


def build_app(registry: Registry, spirit_id: str, factory, *, record_path: str | None = None,
              **session_kwargs) -> web.Application:
    emitter = Emitter(record_path)
    session = VoiceSession(registry, spirit_id, factory, emitter, **session_kwargs)
    app = web.Application()
    app[SESSION] = session
    app[EMITTER] = emitter
    app.router.add_get("/", index)
    app.router.add_get("/events", events_ws)
    app.router.add_get("/io", io_ws)
    app.router.add_static("/ui", ROOT / "ui")
    app.router.add_static("/mascot/assets", ROOT / "mascot" / "assets")
    for name in ("mascot.js", "mascot.css"):
        app.router.add_get("/mascot/" + name, _file_handler(ROOT / "mascot" / name))

    async def on_startup(_app):
        await session.start()

    async def on_cleanup(_app):
        await session.close()
        emitter.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def _file_handler(path: Path):
    async def handler(request: web.Request) -> web.StreamResponse:
        return web.FileResponse(path)
    return handler


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Necromancer voice server (127.0.0.1 only)")
    ap.add_argument("--spirit", default="enron", choices=["enron"])
    ap.add_argument("--db", required=True, help="path to the Enron SQLite index")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--record", help="write emitted UI events to this JSONL file")
    args = ap.parse_args(argv)

    from voice.stream import BedrockFactory

    # Spirits are loaded by name from their data module; the engine imports none statically.
    module = importlib.import_module(f"spirits.{args.spirit}.spirit")
    registry = Registry()
    registry.register(module.make_spirit(args.db))
    app = build_app(registry, args.spirit, BedrockFactory(), record_path=args.record,
                    turn_grace=12.0)
    print(f"Open http://127.0.0.1:{args.port}/ui/index.html"
          f"?ws=ws://127.0.0.1:{args.port}/events&io=ws://127.0.0.1:{args.port}/io")
    web.run_app(app, host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
