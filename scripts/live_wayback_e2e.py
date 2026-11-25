"""Live end-to-end run: real Nova Sonic, summon from Enron Ghost to Wayback Medium, silent-audio mode.
    NECRO_LIVE=1 .venv/bin/python scripts/live_wayback_e2e.py [--out DIR] [--tag TAG] [--enron-db PATH]
    (NECRO_WAYBACK_CACHE=DIR points the run at another cache, for example the main checkout's)
One summon request and the natural demo question ("Summon pets.com in 1999. What were you selling?"),
which needs list_snapshots(url, year) and then read_snapshot(url, timestamp) from the pre-warmed
cache (scripts/prewarm_wayback.py --demo). No network: the Wayback fetch function raises, so any
network use shows up as a failure. Writes a report that lists what the tools returned next to what
the model said, with a verbatim check of every double-quoted span against the cached snapshot text,
the spoken date against the tool result, and the engine's own grounding check. Credentials are
never printed or written.
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
sys.path.insert(0, str(ROOT / "scripts"))
from live_enron_e2e import ask, scrub  # noqa: E402
from voice.emitter import Emitter  # noqa: E402
from voice.grounding_check import COLD, check_turn, date_mentions, normalize, quoted_spans  # noqa: E402
from voice.session import VoiceSession  # noqa: E402
from voice.stream import BedrockFactory  # noqa: E402
from voice.summon import Registry, Spirit  # noqa: E402
SITE = "http://www.pets.com/"
YEAR = "1999"
SUMMON_REQUEST = "Please summon the Wayback Medium."
QUESTION = "Summon pets.com in 1999. What were you selling?"
DEFAULT_OUT = ROOT / ".agents" / "tasks" / "wayback-polish" / "artifacts"
CACHE_DIR = Path(__import__("os").environ.get("NECRO_WAYBACK_CACHE") or ROOT / "data" / "wayback")
TS_RE = re.compile(r"\b\d{14}\b")
URL_RE = re.compile(r"https?://[^\s\"',;]+|www\.[^\s\"',;]+", re.I)
DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
               "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
NET_CALLS: list[str] = []
def spoken_timestamps(text: str) -> set[str]:
    """14-digit timestamps written as digits or spelled out digit by digit (as the voice transcript does)."""
    found = set(TS_RE.findall(text))
    run: list[str] = []
    for w in re.findall(r"[a-z]+|\d", text.lower()) + ["."]:
        d = DIGIT_WORDS.get(w) or (w if w.isdigit() else None)
        if d:
            run.append(d)
            continue
        if len("".join(run)) == 14:
            found.add("".join(run))
        run = []
    return found
def no_network(url: str):
    NET_CALLS.append(url)
    raise RuntimeError(f"network use attempted in live check: {url}")
def build_enron(db: str | None) -> tuple[Spirit, str]:
    from spirits.enron import spirit as enron
    if db and Path(db).is_file():
        return enron.make_spirit(db), f"real Enron index at {db}"
    default = ROOT / "data" / "enron" / "enron.sqlite"
    if default.is_file():
        return enron.make_spirit(str(default)), f"real Enron index at {default}"
    return (Spirit("enron", enron.PERSONA, enron.VOICE_ID, (), "mascot/costumes/enron.json"),
            "STUB enron spirit: real persona, voice and costume, NO tools and no data (no index present)")
def cached_text(url: str = SITE, timestamp: str | None = None) -> str:
    """Text of a cached snapshot; with no timestamp, a probe that the demo listing is cached."""
    from spirits.wayback import tools
    if timestamp is None:
        res = tools.list_snapshots(url, YEAR, cache_dir=str(CACHE_DIR), fetch=no_network)
        return res.get("content") or ""
    res = tools.read_snapshot(url, timestamp, cache_dir=str(CACHE_DIR), fetch=no_network)
    return res.get("content") or ""
def short(v, n=300):
    return v[:n] if isinstance(v, str) else v
def render(turns: list[dict], session: VoiceSession, emitter: Emitter, enron_note: str,
           snap_text: str) -> tuple[str, dict]:
    ev = emitter.events
    spirit_events = [e["spirit"] for e in ev if e["type"] == "spirit"]
    lines = ["# Live Wayback Medium report", "",
             f"- Enron side: {enron_note}",
             f"- Cache: {CACHE_DIR} (listing for {SITE} {YEAR} present, {len(snap_text)} chars)",
             f"- Wayback voice id: tiffany (spirits.wayback.spirit.VOICE_ID); Enron voice id: matthew",
             f"- Spirit events in order: {spirit_events}",
             f"- Network fetch attempts by the wayback tools: {NET_CALLS or 'none'}", ""]
    summary: dict = {"enron_note": enron_note, "spirit_events": spirit_events,
                     "network_attempts": list(NET_CALLS), "turns": []}
    for r in turns:
        lines += [f"## Turn: {r['question']}", "", f"Audio bytes this turn: {r['audio_bytes']}", "",
                  "### Tool calls and what they returned"]
        for t in r["tools"]:
            res = t["result"]
            lines.append(f"- `{t['name']}` args={json.dumps(t['args'])}")
            lines.append(f"  - summary: {res.get('summary')!r}; source: {res.get('source')!r}; "
                         f"error: {res.get('error')!r}")
            lines.append(f"  - content (first 300 chars): {short(res.get('content'))!r}")
            lines.append(f"  - evidence metadata: {json.dumps(res.get('evidence'))}")
        if not r["tools"]:
            lines.append("- (no tool was called)")
        lines += ["", "### Evidence events (kind, metadata)"]
        evs = [e for e in r["events"] if e["type"] == "evidence"]
        for e in evs:
            lines.append(f"- kind={e.get('kind')!r} source={e.get('source')!r} metadata="
                         f"{json.dumps(e.get('metadata'))} content_len="
                         f"{len(e['content']) if isinstance(e.get('content'), str) else None}")
        if not evs:
            lines.append("- (none)")
        lines += ["", "### What was said (final spirit transcript)"]
        result_ts = {m.get("timestamp") for t in r["tools"] for m in t["result"].get("evidence") or []}
        result_urls = {m.get("url") for t in r["tools"] for m in t["result"].get("evidence") or []}
        contents = [normalize(t["result"]["content"]) for t in r["tools"]
                    if isinstance(t["result"].get("content"), str)]
        # Independent re-read of every snapshot the model was shown, straight from the cache.
        reads = [m for t in r["tools"] if t["name"] == "read_snapshot"
                 for m in t["result"].get("evidence") or []]
        snap_n = " ".join(normalize(cached_text(m["url"], m["timestamp"])) for m in reads)
        allowed_dates = {t["result"].get("spoken_date") for t in r["tools"]} | {
            m.get("spoken_date") for t in r["tools"] for m in t["result"].get("evidence") or []}
        flags, quote_checks = [], []
        results = [t["result"] for t in r["tools"]]
        for text in r["spirit_turns"]:
            lines.append(f"> {text}")
            for span in quoted_spans(text):
                n = normalize(span)
                q = {"quote": span, "in_cached_snapshot": n in snap_n,
                     "in_tool_content": any(n in c for c in contents)}
                quote_checks.append(q)
                lines.append(f"- quote check: in_cached_snapshot={q['in_cached_snapshot']} "
                             f"in_tool_content={q['in_tool_content']} :: {span!r}")
                if not q["in_cached_snapshot"]:
                    flags.append(f"quote not in cached snapshot: {span!r}")
            said_ts = spoken_timestamps(text)
            said_urls = {u.rstrip(".") for u in URL_RE.findall(text)}
            lines.append(f"- dates stated (month, day, year): {date_mentions(text) or 'none'}; "
                         f"spoken_date values in tool results: {sorted(d for d in allowed_dates if d)}")
            lines.append(f"- 14-digit timestamps stated: {sorted(said_ts) or 'none'}; URLs stated: "
                         f"{sorted(said_urls) or 'none'}")
            for p in check_turn(text, results, spirit="wayback"):
                flags.append("grounding_check: " + p)
            if said_ts - result_ts:
                flags.append(f"timestamp not from tool result: {sorted(said_ts - result_ts)}")
            if reads and not date_mentions(text):
                flags.append("no spoken date stated")
        if not r["spirit_turns"]:
            lines.append("> (no final spirit text)")
            flags.append("no final spirit text")
        for f in flags:
            lines.append(f"- FLAG: {f}")
        lines.append("")
        summary["turns"].append({
            "question": r["question"], "tools": [t["name"] for t in r["tools"]],
            "tool_summaries": [t["result"].get("summary") for t in r["tools"]],
            "evidence_events": [{"kind": e.get("kind"), "metadata": e.get("metadata")} for e in evs],
            "spirit_turns": r["spirit_turns"], "quote_checks": quote_checks, "flags": flags,
            "audio_bytes": r["audio_bytes"]})
    lines += ["## Session totals", "",
              f"- audio bytes: {session.audio_bytes}",
              f"- final active spirit: {session.spirit.id}",
              f"- engine grounding violations: {session.grounding_violations or 'none'}",
              f"- emitter protocol violations: {emitter.violations or 'none'}",
              f"- errors emitted: {[e for e in ev if e['type'] == 'error'] or 'none'}", ""]
    return "\n".join(lines), summary
async def wait_spirit(emitter: Emitter, name: str, timeout: float = 90.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if sum(1 for e in emitter.events if e["type"] == "spirit" and e["spirit"] == name):
            return True
        await asyncio.sleep(0.25)
    return False
async def run(out: Path, tag: str, enron_db: str | None) -> int:
    from spirits.wayback.spirit import make_spirit as make_wayback
    out.mkdir(parents=True, exist_ok=True)
    snap_text = cached_text()
    if not snap_text:
        print("No cached listing; run scripts/prewarm_wayback.py --demo first.", file=sys.stderr)
        return 3
    enron_spirit, enron_note = build_enron(enron_db)
    registry = Registry()
    registry.register(enron_spirit)
    registry.register(make_wayback(cache_dir=str(CACHE_DIR), fetch=no_network))
    emitter = Emitter(str(out / f"events_live{tag}.jsonl"))
    session = VoiceSession(registry, "enron", BedrockFactory(), emitter, turn_grace=12.0)
    turns = []
    await session.start()
    try:
        turns.append(await ask(session, emitter, SUMMON_REQUEST, timeout=90.0))
        arrived = await wait_spirit(emitter, "wayback")
        if arrived:
            await asyncio.sleep(3.0)
            turns.append(await ask(session, emitter, QUESTION))
    finally:
        await session.close()
        emitter.close()
    report, summary = render(turns, session, emitter, enron_note, snap_text)
    if not arrived:
        report += "\n**The summon never produced a `spirit` event for wayback; the question was not asked.**\n"
    (out / f"report{tag}.md").write_text(scrub(report), encoding="utf-8")
    (out / f"report{tag}.json").write_text(scrub(json.dumps(summary, indent=2)), encoding="utf-8")
    print(scrub(report))
    return 0 if arrived and len(turns) == 2 and turns[1]["spirit_turns"] else 1
def main() -> int:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--tag", default="_wayback")
    ap.add_argument("--enron-db", default=None)
    args = ap.parse_args()
    if os.environ.get("NECRO_LIVE") != "1":
        print("Set NECRO_LIVE=1 to run against real AWS.", file=sys.stderr)
        return 2
    return asyncio.run(run(Path(args.out), args.tag, args.enron_db))
if __name__ == "__main__":
    sys.exit(main())
