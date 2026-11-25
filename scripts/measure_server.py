"""Measure the voice server's memory and startup time with the real Enron index. OFFLINE.

No Bedrock call is made: the server runs with the scripted fake stream from tests/fake_bedrock.py.
The index is opened read-only by the normal search tools; this script never writes to it.

  .venv/bin/python scripts/measure_server.py --db data/enron/enron.sqlite [--clients 5] [--rounds 6]
         [--no-sdk] [--warm] [--json out.json]

The parent process starts the server as a child (`--child`), samples /proc/<pid>/status while
N websocket clients drive it, and prints one JSON document. The server allows ONE live Bedrock
session at a time, so "concurrent sessions" here means N browsers connected at once, one of them
starting the live session, all sending audio, and many overlapping search tool calls. Live
sessions are then started and stopped `--rounds` times to look for a memory leak.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CODE = "measure-only-code"
QUERIES = ["California energy crisis", "raptor partnership", "Skilling resign", "trading desk gas",
           "board meeting minutes", "mark to market accounting", "broadband Portland",
           "Dabhol power India", "merger Dynegy", "retention bonus", "audit committee", "pipeline outage"]
STEPS = 40
FIRST = {"search": "q0: first query", "who": "who: first",          "sender": "sender: first"}


# ------------------------------------------------------------------ child: the server
def run_child(db: str, port: int, import_sdk: bool) -> None:
    from aiohttp import web
    from tests import fake_bedrock as fb
    from voice.guard import AccessGate, CapStore, Limits
    from voice.server import build_app, load_registry
    from voice.usage import UsageLog

    if import_sdk:  # what the first real live session imports, so RSS includes it
        from aws_sdk_bedrock_runtime.aio.client import AsyncBedrockRuntimeClient  # noqa: F401
        from aws_sdk_bedrock_runtime.aio.config import AsyncBedrockRuntimeConfig  # noqa: F401
        from smithy_http.aio.crt import AWSCRTHTTPClient, AWSCRTHTTPClientConfig
        AWSCRTHTTPClient(AWSCRTHTTPClientConfig(force_http_2=True))  # loads the native awscrt library

    class Factory:
        async def open(self):
            steps = []
            for i in range(STEPS):
                q = QUERIES[i % len(QUERIES)]
                steps.append(fb.on_text(f"q{i}:", fb.completion_start(),
                                        fb.tool_use("search_emails", f"tu-{i}", {"query": q}),
                                        fb.assistant_text("Searching.", "FINAL", "END_TURN"),
                                        fb.audio_out(b"\x01\x02" * 2400), fb.completion_end()))
            for key, name, args in (("who:", "who_wrote_to", {"person": "kenneth.lay"}),
                                    ("person:", "find_person", {"name": "Jeff Skilling"}),
                                    ("sender:", "search_emails", {"query": "meeting", "sender": "lay"}),
                                    ("read:", "read_email", {"evidence_id": "nope"})):
                steps.append(fb.on_text(key, fb.completion_start(), fb.tool_use(name, "tu-" + key, args),
                                        fb.assistant_text("Searching.", "FINAL", "END_TURN"),
                                        fb.completion_end()))
            s = fb.FakeStream(steps)
            return s

    limits = Limits(max_session_seconds=120, idle_seconds=120, daily_cap=10_000, total_cap=10_000)
    tmp = tempfile.mkdtemp(prefix="necro-measure-")
    registry = load_registry("enron", db)
    app = build_app(registry, "enron", Factory(), gate=AccessGate(CODE), limits=limits,
                    caps=CapStore(os.path.join(tmp, "caps.json"), limits),
                    usage_log=UsageLog(os.path.join(tmp, "usage.jsonl")), public=True)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


# ------------------------------------------------------------------ parent helpers
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def proc_kb(pid: int) -> dict:
    out = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(("VmRSS", "VmHWM", "VmSwap", "Threads")):
                k, v = line.split(":")
                out[k] = int(v.split()[0])
    except OSError:
        pass
    return out


def smaps_kb(pid: int) -> dict:
    out = {}
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines()[1:]:
            k, _, v = line.partition(":")
            if v.strip().endswith("kB"):
                out[k] = int(v.split()[0])
    except OSError:
        pass
    return out


def drop_file_cache(path: str) -> None:
    """Evict the file's clean pages (no root needed). Same effect as a cold disk cache for this file."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(fd)


def cached_pages(path: str) -> str:
    try:
        r = subprocess.run(["fincore", "--bytes", "--noheadings", "-o", "RES,SIZE", path],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip() or r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "fincore unavailable"


async def authed(session, url: str):
    ws = await session.ws_connect(url, max_msg_size=1 << 22)
    first = await ws.receive_json(timeout=10)
    assert first.get("reason") == "code_required", first
    await ws.send_json({"type": "auth", "code": CODE})
    return ws


async def main_parent(args) -> dict:
    import aiohttp

    db = str(Path(args.db).resolve())
    result: dict = {"db": db, "db_bytes": os.path.getsize(db), "clients": args.clients,
                    "rounds": args.rounds, "sdk_imported": not args.no_sdk, "warm_cache": args.warm, "first_tool": args.first}
    if args.warm:
        with open(db, "rb") as f:
            while f.read(1 << 24):
                pass
    else:
        drop_file_cache(db)
    result["cache_before_start"] = cached_pages(db)

    port = free_port()
    cmd = [sys.executable, str(Path(__file__).resolve()), "--child", "--db", db, "--port", str(port)]
    if args.no_sdk:
        cmd.append("--no-sdk")
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)})
    samples: list[int] = []
    stop = False

    async def sampler():
        while not stop:
            r = proc_kb(proc.pid).get("VmRSS")
            if r:
                samples.append(r)
            await asyncio.sleep(0.05)

    sample_task = asyncio.create_task(sampler())
    try:
        async with aiohttp.ClientSession() as http:
            while True:
                if proc.poll() is not None:
                    raise RuntimeError("server exited during startup")
                try:
                    async with http.get(f"http://127.0.0.1:{port}/ui/index.html") as r:
                        if r.status == 200:
                            break
                except aiohttp.ClientError:
                    await asyncio.sleep(0.02)
            result["startup_seconds"] = round(time.monotonic() - t0, 3)
            result["rss_kb_after_startup"] = proc_kb(proc.pid).get("VmRSS")
            result["cache_after_start"] = cached_pages(db)

            base = f"ws://127.0.0.1:{port}"
            ev_socks, io_socks = [], []
            for _ in range(args.clients):
                ev_socks.append(await authed(http, base + "/events"))
                io_socks.append(await authed(http, base + "/io"))
            counts = {"evidence": 0, "errors": 0, "other": 0}

            async def drain(ws):
                async for m in ws:
                    if m.type == aiohttp.WSMsgType.TEXT:
                        ev = json.loads(m.data)
                        t = ev.get("type")
                        if ws is not ev_socks[0]:
                            counts["other"] += 1
                        elif t == "evidence":
                            counts["evidence"] += 1
                        elif t == "error":
                            counts["errors"] += 1
                        else:
                            counts["other"] += 1

            drains = [asyncio.create_task(drain(w)) for w in ev_socks + io_socks]

            async def wait_evidence(n, timeout=60):
                end = time.monotonic() + timeout
                while counts["evidence"] < n:
                    if time.monotonic() > end:
                        raise TimeoutError(f"evidence {counts['evidence']} < {n}")
                    await asyncio.sleep(0.01)

            # ---- cold first query (first FTS lookup after a cold cache)
            await io_socks[0].send_json({"type": "start"})
            await asyncio.sleep(0.5)
            t1 = time.monotonic()
            await io_socks[0].send_json({"type": "text", "text": FIRST[args.first]})
            await wait_evidence(1)
            result["first_query_seconds"] = round(time.monotonic() - t1, 3)
            result["rss_kb_after_first_query"] = proc_kb(proc.pid).get("VmRSS")
            result["cache_after_first_query"] = cached_pages(db)

            # ---- steady queries, one at a time (warm)
            lat = []
            for i in range(1, 9):
                before = counts["evidence"]
                t = time.monotonic()
                await io_socks[0].send_json({"type": "text", "text": f"q{i}: next"})
                await wait_evidence(before + 1)
                lat.append(time.monotonic() - t)
            result["warm_query_seconds"] = {"min": round(min(lat), 3), "max": round(max(lat), 3),
                                            "mean": round(sum(lat) / len(lat), 3)}

            # ---- concurrent load: every client streams audio while all clients send text at once
            pcm = b"\x00\x01" * 1600  # 100 ms of 16 kHz PCM

            async def audio_client(ws, seconds=4.0):
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    await ws.send_bytes(pcm)
                    await asyncio.sleep(0.1)

            before = counts["evidence"]
            texts = [{"type": "text", "text": f"q{9 + i}: parallel"} for i in range(min(30, STEPS - 9))]

            async def burst(ws, items):
                for it in items:
                    await ws.send_json(it)
                    await asyncio.sleep(0.02)

            per = max(1, len(texts) // args.clients)
            await asyncio.gather(
                *[audio_client(w) for w in io_socks],
                *[burst(w, texts[i * per:(i + 1) * per]) for i, w in enumerate(io_socks)])
            await asyncio.sleep(2)
            result["concurrent_texts_sent"] = per * args.clients
            result["concurrent_evidence_received"] = counts["evidence"] - before
            result["rss_kb_after_concurrent"] = proc_kb(proc.pid).get("VmRSS")

            # ---- repeated live sessions on a fresh stream each time: leak check
            rss_rounds = []
            for r in range(args.rounds):
                # a new stream is only opened by a start after the previous session ended; the
                # simplest end is a client-initiated disconnect of every /io socket, then reconnect
                for w in io_socks:
                    await w.close()
                await asyncio.sleep(6.5)  # disconnect_grace is 5 s
                io_socks = [await authed(http, base + "/io") for _ in range(args.clients)]
                drains += [asyncio.create_task(drain(w)) for w in io_socks]
                await io_socks[0].send_json({"type": "start"})
                await asyncio.sleep(0.5)
                before = counts["evidence"]
                await io_socks[0].send_json({"type": "text", "text": "q0: again"})
                try:
                    await wait_evidence(before + 1, 20)
                except TimeoutError:
                    pass
                rss_rounds.append(proc_kb(proc.pid).get("VmRSS"))
            result["rss_kb_each_round"] = rss_rounds
            result["errors_seen"] = counts["errors"]

            st = proc_kb(proc.pid)
            result["rss_kb_final"] = st.get("VmRSS")
            result["rss_kb_peak_kernel_hwm"] = st.get("VmHWM")
            result["threads"] = st.get("Threads")
            result["smaps_rollup_kb"] = {k: v for k, v in smaps_kb(proc.pid).items()
                                         if k in ("Rss", "Pss", "Pss_Anon", "Pss_File", "Anonymous")}
            for d in drains:
                d.cancel()
            for w in ev_socks + io_socks:
                await w.close()
    finally:
        stop = True
        await sample_task
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    result["rss_kb_peak_sampled_50ms"] = max(samples) if samples else None
    result["rss_mb_peak_kernel_hwm"] = round(result["rss_kb_peak_kernel_hwm"] / 1024, 1)
    result["rss_mb_peak_sampled"] = round(result["rss_kb_peak_sampled_50ms"] / 1024, 1)
    return result


def direct(db: str) -> dict:
    from spirits.enron import tools
    db = str(Path(db).resolve())
    calls = {
        "search_emails(query)": lambda: tools.search_emails(db, "California energy crisis", 5),
        "search_emails(query+sender)": lambda: tools.search_emails(db, "meeting", 5, None, None, "lay"),
        "who_wrote_to(person)": lambda: tools.who_wrote_to(db, "kenneth.lay", None, None, 10),
        "find_person(name)": lambda: tools.find_person(db, "Jeff Skilling"),
        "read_email(id)": lambda: tools.read_email(db, "nope"),
    }
    out: dict = {"db_bytes": os.path.getsize(db)}
    for name, fn in calls.items():
        drop_file_cache(db)
        t = time.monotonic(); fn(); cold = time.monotonic() - t
        t = time.monotonic(); fn(); warm = time.monotonic() - t
        out[name] = {"cold_s": round(cold, 3), "second_call_s": round(warm, 3)}
    drop_file_cache(db)
    t = time.monotonic()
    with open(db, "rb") as f:
        while f.read(1 << 24):
            pass
    out["sequential_read_whole_file_cold_s"] = round(time.monotonic() - t, 2)
    out["cache_after_sequential_read"] = cached_pages(db)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--clients", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--no-sdk", action="store_true", help="do not import the Bedrock SDK in the server")
    ap.add_argument("--warm", action="store_true", help="read the index into the page cache first")
    ap.add_argument("--first", choices=sorted(FIRST), default="search",
                    help="which tool the first (cold) query uses")
    ap.add_argument("--json", help="also write the result here")
    ap.add_argument("--direct", action="store_true",
                    help="no server: time each Enron tool once on a cold cache, then warm")
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    if args.child:
        run_child(args.db, args.port, not args.no_sdk)
        return
    if args.direct:
        result = direct(args.db)
    else:
        result = asyncio.run(main_parent(args))
    text = json.dumps(result, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + "\n")


if __name__ == "__main__":
    main()
