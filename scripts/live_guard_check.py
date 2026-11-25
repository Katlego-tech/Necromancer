"""Live check of the cost guardrails against real Nova Sonic (one billed run, about 20 seconds).
    NECRO_LIVE=1 .venv/bin/python scripts/live_guard_check.py [/path/to/enron.sqlite]
Starts a LiveController with a 20 s session cap, sends one short typed question, waits for the
cap to close the stream, then checks the summary line, usage log line, paused event, closed stream
and leftover tasks. Caps and the usage log live in a temp dir. Credentials are never printed.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from voice.emitter import Emitter  # noqa: E402
from voice.guard import CapStore, Limits  # noqa: E402
from voice.live import LiveController  # noqa: E402
from voice.session import VoiceSession  # noqa: E402
from voice.stream import BedrockFactory  # noqa: E402
from voice.summon import Registry  # noqa: E402
from voice.usage import UsageLog  # noqa: E402
DEFAULT_DB = "/home/kirito/Documents/projects/personal/Necromancer/data/enron/enron.sqlite"
QUESTION = "Say hello in one short sentence."
CAP_SECONDS = 20
async def run(db: str) -> int:
    from spirits.enron.spirit import make_spirit
    tmp = tempfile.mkdtemp(prefix="necro-guard-")
    registry = Registry()
    registry.register(make_spirit(db))
    emitter = Emitter(None)
    lines: list[str] = []
    factory = BedrockFactory()
    ctl = LiveController(
        lambda: VoiceSession(registry, "enron", factory, emitter, turn_grace=12.0),
        emitter, Limits(max_session_seconds=CAP_SECONDS, idle_seconds=60),
        CapStore(os.path.join(tmp, "caps.json"), Limits()), UsageLog(os.path.join(tmp, "usage.jsonl")),
        factory=factory, print_fn=lambda s: (lines.append(s), print(s)))
    before = {t for t in asyncio.all_tasks()}
    t0 = time.monotonic()
    ok = await ctl.ensure_started()
    session = ctl.session
    print("started:", ok)
    if not ok:
        return 1
    await session.send_text(QUESTION)
    deadline = time.monotonic() + 30
    while ctl.state != "off" and time.monotonic() < deadline:
        await asyncio.sleep(0.25)
    elapsed = time.monotonic() - t0
    await ctl.shutdown()
    paused = [e for e in emitter.events if e.get("type") == "paused"]
    spirit_final = [e for e in emitter.events if e.get("type") == "transcript"
                    and e.get("role") == "spirit" and e.get("final")]
    log_lines = Path(tmp, "usage.jsonl").read_text().splitlines() if Path(tmp, "usage.jsonl").exists() else []
    record = json.loads(log_lines[0]) if log_lines else {}
    tot = record.get("usage", {})
    flat = sum(tot.get(d, {}).get(k, 0) for d in ("input", "output") for k in ("speechTokens", "textTokens"))
    await asyncio.sleep(0.2)
    stray = [t for t in asyncio.all_tasks() if t not in before and t is not asyncio.current_task() and not t.done()]
    checks = {
        "summary printed": len(lines) == 1 and lines[0].startswith("usage: "),
        "one usage log line": len(log_lines) == 1,
        "non-zero tokens": flat > 0,
        "paused session_limit": [e.get("reason") for e in paused] == ["session_limit"],
        "spirit answered": bool(spirit_final),
        "stream closed": session._closing and session._active is None,
        "no stray tasks": not stray,
        "stopped near cap": CAP_SECONDS - 1 <= elapsed <= CAP_SECONDS + 10,
    }
    print("elapsed_seconds: %.1f" % elapsed)
    print("usage:", json.dumps(tot))
    print("seconds:", record.get("seconds"), "streams:", record.get("streams"))
    print("spirit said:", [e.get("text") for e in spirit_final][:1])
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    return 0 if all(checks.values()) else 1
def main() -> int:
    if os.environ.get("NECRO_LIVE") != "1":
        print("Set NECRO_LIVE=1 to run against real AWS.", file=sys.stderr)
        return 2
    return asyncio.run(run(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB))
if __name__ == "__main__":
    sys.exit(main())
