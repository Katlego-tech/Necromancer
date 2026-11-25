"""Live rehearsal of the demo's Grid Ghost moment on a real Nova Sonic stream (billed, typed input).

    NECRO_LIVE=1 .venv/bin/python scripts/live_demo_moment.py data/enron/enron.sqlite [--out DIR]

Sequence: (1) an Enron question, (2) the replay alert fires in the middle of that conversation,
(3) an Enron follow-up proves the hand-back works. Prints what each spirit said next to what the
tools and the schedule record returned. The alert data is a SYNTHETIC replay fixture.
Credentials are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from live_enron_e2e import ask, scrub  # noqa: E402
from spirits.enron.spirit import make_spirit as make_enron  # noqa: E402
from spirits.grid import spirit as grid  # noqa: E402
from voice.emitter import Emitter  # noqa: E402
from voice.session import VoiceSession  # noqa: E402
from voice.stream import BedrockFactory  # noqa: E402
from voice.summon import Registry  # noqa: E402

Q1 = "What was Ken Lay told about Raptor?"
Q2 = "Who else wrote to Ken Lay about the Raptor transactions?"


def seq(events, kind):
    return [e.get(kind) for e in events if e["type"] == kind]


def final_turns(events):
    turns = {}
    for e in events:
        if e["type"] == "transcript" and e["role"] == "spirit" and e["final"]:
            turns[e["turn_id"]] = e["text"]
    return list(turns.values())


async def run(db: str, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    alert = grid.build_alert()
    if alert is None:
        print("alert is cold; check data/fixtures/grid_replay.json")
        return 1
    registry = Registry()
    registry.register(make_enron(db))
    registry.register(grid.make_spirit())
    emitter = Emitter(str(out / "events_demo_moment.jsonl"))
    session = VoiceSession(registry, "enron", BedrockFactory(), emitter, turn_grace=12.0)
    report: dict = {"record": {k: alert[k] for k in ("stage", "area", "spoken_start")}}
    await session.start()
    try:
        r1 = await ask(session, emitter, Q1)
        report["q1"] = {"said": r1["spirit_turns"], "tools": [t["name"] for t in r1["tools"]]}
        await asyncio.sleep(2)
        mark = len(emitter.events)
        started = await session.alert("grid", alert["prompt"], evidence=alert["evidence"],
                                      expected=(alert["stage"], alert["area"], alert["spoken_start"]),
                                      line=alert["line"])
        t0 = time.monotonic()
        while session._alert is not None and time.monotonic() - t0 < 90:
            await asyncio.sleep(0.25)
        await asyncio.sleep(3)
        ev = emitter.events[mark:]
        report["alert"] = {"start_result": started, "finished": session._alert is None,
                           "said": final_turns(ev), "spirits": seq(ev, "spirit"),
                           "states": seq(ev, "state"),
                           "evidence": [{"kind": e.get("kind"), "source": e.get("source"),
                                         "metadata": e.get("metadata")}
                                        for e in ev if e["type"] == "evidence"]}
        r2 = await ask(session, emitter, Q2)
        report["q2"] = {"said": r2["spirit_turns"], "tools": [t["name"] for t in r2["tools"]],
                        "evidence_kinds": [e.get("kind") for e in r2["events"] if e["type"] == "evidence"]}
        report["active_spirit_at_end"] = session.spirit.id
        report["grounding_violations"] = list(session.grounding_violations)
        report["emitter_violations"] = list(emitter.violations)
        report["errors"] = [e for e in emitter.events if e["type"] == "error"]
        report["audio_bytes"] = session.audio_bytes
    finally:
        await session.close()
        emitter.close()
    text = scrub(json.dumps(report, indent=2, default=str))
    (out / "report_demo_moment.json").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--out", default=str(ROOT / ".agents" / "tasks" / "grid-ghost" / "artifacts"))
    args = ap.parse_args()
    if os.environ.get("NECRO_LIVE") != "1":
        print("Set NECRO_LIVE=1 to run against real AWS (billed).", file=sys.stderr)
        return 2
    return asyncio.run(run(args.db, Path(args.out)))


if __name__ == "__main__":
    sys.exit(main())
