"""Spike: engine-initiated Grid Ghost alert turn on a live Nova Sonic stream (no user prompt).

    NECRO_LIVE=1 .venv/bin/python scripts/spike_grid_alert.py --variant iso|spoken [--idle 4]

Opens one session with silent audio streaming, waits --idle seconds, injects ONE cross-modal USER text
turn built by alert_text(), and records the spirit transcript, audio bytes and token usage.
Uses the 'default' AWS profile, us-east-1. Credentials are never printed.
"""
from __future__ import annotations
import argparse, asyncio, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from spirits.grid import tools  # noqa: E402
from voice.emitter import Emitter  # noqa: E402
from voice.session import VoiceSession  # noqa: E402
from voice.stream import BedrockFactory  # noqa: E402
from voice.summon import Registry, Spirit  # noqa: E402

PERSONA = ("You are the Grid Ghost, a load-shedding alert voice. You only state facts given to you "
           "in alert messages. Never add numbers, names or times of your own. Keep it to one or two sentences.")
SPOKEN = {"iso": None,
          "spoken": "6 PM on 30 November 2026"}


def alert_text(result: dict, variant: str) -> str:
    e = result["evidence"][0]
    start = e["start"] if variant == "iso" else SPOKEN["spoken"]
    return ("ALERT FROM THE SCHEDULE SYSTEM, not from the user. Warn the listener now, out loud, and say "
            f"these three values exactly as written: stage {e['stage']}; area {e['area']}; start time {start}. "
            "Begin with the word Warning. Add nothing else.")


async def main(variant: str, idle: float) -> int:
    if os.environ.get("NECRO_LIVE") != "1":
        print("set NECRO_LIVE=1"); return 2
    result = tools.next_outage("Sandton (Demo Area 1)")
    reg = Registry(); reg.register(Spirit("grid", PERSONA, "tiffany", (), None))
    em = Emitter(); s = VoiceSession(reg, "grid", BedrockFactory(), em)
    await s.start()
    t_inject = None
    try:
        await asyncio.sleep(idle)
        pre_audio = s.audio_bytes
        text = alert_text(result, variant)
        t_inject = time.monotonic()
        await s.inject_text(text)
        last = time.monotonic()
        n = len(em.events)
        while time.monotonic() - t_inject < 40:
            await asyncio.sleep(0.25)
            if len(em.events) != n:
                n, last = len(em.events), time.monotonic()
            done = any(e["type"] == "transcript" and e["role"] == "spirit" and e["final"] for e in em.events)
            if done and time.monotonic() - last > 3:
                break
    finally:
        await s.close()
    finals = {e["turn_id"]: e["text"] for e in em.events
              if e["type"] == "transcript" and e["role"] == "spirit" and e["final"]}
    print(json.dumps({"variant": variant, "idle_s": idle, "alert_text": text, "audio_before_alert": pre_audio,
                      "audio_total": s.audio_bytes, "spirit_turns": list(finals.values()),
                      "user_turns_in_transcript": [t for t in s.transcript if t["role"] == "user"],
                      "errors": [e for e in em.events if e["type"] == "error"],
                      "usage": s.usage.totals()}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--variant", default="iso"); ap.add_argument("--idle", type=float, default=4)
    a = ap.parse_args(); sys.exit(asyncio.run(main(a.variant, a.idle)))
