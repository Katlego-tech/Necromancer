"""Live check: trigger the Grid Ghost replay alert on a real Nova Sonic stream (one billed session).
    NECRO_LIVE=1 .venv/bin/python scripts/live_grid_alert.py [--idle 4]
Not part of the test suite. Uses the default AWS profile; credentials are never printed.
"""
from __future__ import annotations
import argparse, asyncio, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from spirits.grid import spirit as grid  # noqa: E402
from voice.emitter import Emitter  # noqa: E402
from voice.session import VoiceSession  # noqa: E402
from voice.stream import BedrockFactory  # noqa: E402
from voice.summon import Registry  # noqa: E402


async def main(idle: float) -> int:
    if os.environ.get("NECRO_LIVE") != "1":
        print("set NECRO_LIVE=1")
        return 2
    alert = grid.build_alert()
    if alert is None:
        print("alert is cold")
        return 1
    reg = Registry()
    reg.register(grid.make_spirit())
    em = Emitter()
    s = VoiceSession(reg, "grid", BedrockFactory(), em)
    await s.start()
    try:
        await asyncio.sleep(idle)
        await s.alert("grid", alert["prompt"], evidence=alert["evidence"],
                      expected=(alert["stage"], alert["area"], alert["spoken_start"]), line=alert["line"])
        while s._alert is not None:
            await asyncio.sleep(0.25)
    finally:
        await s.close()
    for e in em.events:
        if e["type"] == "transcript" and e["final"]:
            print(e["role"], e["text"])
    print("grounding violations:", s.grounding_violations)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle", type=float, default=4.0)
    sys.exit(asyncio.run(main(ap.parse_args().idle)))
