"""Live end-to-end run: real Nova Sonic + Enron Ghost on the real index, silent-audio mode.

    NECRO_LIVE=1 .venv/bin/python scripts/live_enron_e2e.py /path/to/enron.sqlite [--out DIR] [--tag TAG]

Asks three typed questions, records every UI event and writes a report that lists what the
tools returned next to what the model said, with per-quote substring checks against the tool
content and against the raw maildir file. Credentials are never printed or written.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voice.emitter import Emitter  # noqa: E402
from voice.grounding_check import COLD, normalize, quoted_spans  # noqa: E402
from voice.session import VoiceSession  # noqa: E402
from voice.stream import BedrockFactory  # noqa: E402
from voice.summon import Registry  # noqa: E402

QUESTIONS = [
    "What did executives say about the Raptor transactions?",
    "What was Ken Lay told about Raptor?",
    "What did anyone write about the zxqvblorp quantum pineapple merger?",
]
DEFAULT_OUT = ROOT / ".agents" / "tasks" / "m2-voice" / "artifacts"
SECRET_RE = re.compile(r"(AKIA|ASIA)[A-Z0-9]{12,}|aws_secret\w*|SessionToken", re.I)


def scrub(text: str) -> str:
    return SECRET_RE.sub("[redacted]", text)


async def ask(session: VoiceSession, emitter: Emitter, question: str, timeout: float = 120.0,
              quiet: float = 3.0) -> dict:
    start_events = len(emitter.events)
    start_tools = len(session.tool_log)
    start_audio = session.audio_bytes
    await session.send_text(question)
    t0 = time.monotonic()
    last_n, last_change = start_events, time.monotonic()
    while time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.25)
        n = len(emitter.events)
        if n != last_n:
            last_n, last_change = n, time.monotonic()
        new = emitter.events[start_events:]
        done = any(e["type"] == "transcript" and e["role"] == "spirit" and e["final"] for e in new)
        if done and time.monotonic() - last_change > quiet:
            break
    new = emitter.events[start_events:]
    turns: dict[str, str] = {}
    for e in new:
        if e["type"] == "transcript" and e["role"] == "spirit" and e["final"]:
            turns[e["turn_id"]] = e["text"]
    return {"question": question, "events": new, "spirit_turns": list(turns.values()),
            "tools": session.tool_log[start_tools:], "audio_bytes": session.audio_bytes - start_audio}


def check_quotes(text: str, tools: list[dict], maildir: Path) -> list[dict]:
    contents = [normalize(t["result"]["content"]) for t in tools
                if isinstance(t["result"].get("content"), str)]
    raws = []
    for t in tools:
        for ev in t["result"].get("evidence") or []:
            sp = ev.get("source_path")
            if sp and (maildir / sp).is_file():
                raws.append((sp, normalize((maildir / sp).read_text(errors="replace"))))
    out = []
    for span in quoted_spans(text):
        n = normalize(span)
        # the search excerpt marks cut text with "..."; the raw file has no such marker
        raw_n = n.strip(" .\u2026")
        out.append({"quote": span,
                    "in_tool_content": any(n in c for c in contents),
                    "in_raw_files": [sp for sp, r in raws if raw_n in r]})
    return out


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def render(results: list[dict], session: VoiceSession, maildir: Path) -> tuple[str, dict]:
    lines = ["# Live Enron Ghost report", ""]
    summary = {"questions": [], "audio_bytes_total": session.audio_bytes,
               "grounding_violations": list(session.grounding_violations),
               "emitter_violations": list(session.emitter.violations)}
    for i, r in enumerate(results, 1):
        lines += [f"## Q{i}: {r['question']}", ""]
        lines.append(f"Audio bytes received this turn: {r['audio_bytes']}")
        lines.append("")
        lines.append("### Tool calls and what they returned")
        for t in r["tools"]:
            res = t["result"]
            lines.append(f"- `{t['name']}` args={json.dumps(t['args'])}")
            lines.append(f"  - summary: {res.get('summary')!r}; source: {res.get('source')!r}")
            if isinstance(res.get("content"), str):
                lines.append(f"  - content (first 400 chars): {res['content'][:400]!r}")
        if not r["tools"]:
            lines.append("- (no tool was called)")
        lines += ["", "### Evidence events"]
        evs = [e for e in r["events"] if e["type"] == "evidence"]
        for e in evs:
            lines.append(f"- source={e.get('source')!r} content_len="
                         f"{len(e['content']) if isinstance(e.get('content'), str) else None}")
        if not evs:
            lines.append("- (none)")
        lines += ["", "### What the model said (final transcript)"]
        checks_all, ungrounded = [], []
        for text in r["spirit_turns"]:
            lines.append(f"> {text}")
            checks = check_quotes(text, r["tools"], maildir)
            checks_all += checks
            for c in checks:
                if not c["in_tool_content"]:
                    ungrounded.append(c["quote"])
            for c in checks:
                lines.append(f"- quote check: in_tool_content={c['in_tool_content']} "
                             f"in_raw_files={c['in_raw_files']} :: {c['quote']!r}")
        cold_tool = any(t["result"].get("summary") == COLD for t in r["tools"])
        if not r["spirit_turns"]:
            lines.append("> (no final spirit text)")
        said_cold = any(normalize(t) == COLD for t in r["spirit_turns"])
        sentence_flags = []
        for text in r["spirit_turns"]:
            for s in sentences(text):
                if cold_tool and normalize(s) != COLD and not r["spirit_turns"] == []:
                    sentence_flags.append(f"after cold result, extra sentence: {s!r}")
        lines += ["", f"Cold result returned by a tool: {cold_tool}; model said exactly the cold line: {said_cold}"]
        for f in sentence_flags:
            lines.append(f"- FLAG (ungrounded sentence): {f}")
        for q in ungrounded:
            lines.append(f"- FLAG (quote not in tool content): {q!r}")
        lines.append("")
        summary["questions"].append({
            "question": r["question"], "tools": [t["name"] for t in r["tools"]],
            "evidence_events": len(evs), "audio_bytes": r["audio_bytes"],
            "spirit_turns": r["spirit_turns"], "quote_checks": checks_all,
            "cold_tool": cold_tool, "said_cold": said_cold, "flags": sentence_flags + ungrounded})
    lines += ["## Session totals", "",
              f"- audio bytes: {session.audio_bytes}",
              f"- engine grounding violations: {session.grounding_violations or 'none'}",
              f"- emitter protocol violations: {session.emitter.violations or 'none'}", ""]
    return "\n".join(lines), summary


async def run(db: str, out: Path, tag: str) -> int:
    from spirits.enron.spirit import make_spirit
    out.mkdir(parents=True, exist_ok=True)
    maildir = Path(db).parent / "maildir"
    registry = Registry()
    registry.register(make_spirit(db))
    emitter = Emitter(str(out / f"events_live{tag}.jsonl"))
    session = VoiceSession(registry, "enron", BedrockFactory(), emitter, turn_grace=12.0)
    results = []
    await session.start()
    try:
        for q in QUESTIONS:
            results.append(await ask(session, emitter, q))
    finally:
        await session.close()
        emitter.close()
    report, summary = render(results, session, maildir)
    (out / f"report{tag}.md").write_text(scrub(report), encoding="utf-8")
    (out / f"report{tag}.json").write_text(scrub(json.dumps(summary, indent=2)), encoding="utf-8")
    print(scrub(report))
    ok = session.audio_bytes > 0 and all(r["spirit_turns"] for r in results)
    return 0 if ok else 1


def main() -> int:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    if os.environ.get("NECRO_LIVE") != "1":
        print("Set NECRO_LIVE=1 to run against real AWS.", file=sys.stderr)
        return 2
    return asyncio.run(run(args.db, Path(args.out), args.tag))


if __name__ == "__main__":
    sys.exit(main())
