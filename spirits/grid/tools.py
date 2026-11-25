"""Grid Ghost tools. Every tool returns a ToolResult dict:
    {"source": <citable id or None>, "content": <raw evidence or None>,
     "summary": <str>, "evidence": [<metadata dict>, ...]}
Alerts only: nothing here controls or suggests controlling devices.

Replay mode (default) reads only the synthetic fixture. Summaries state stage, area
and start time exactly as the record holds them: no rounding, no inference.

Live mode (EskomSePush) is a STUB. The live API shape has not been verified against
official documentation, so no request is built or sent. It returns the cold trail.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from typing import Callable, Optional

COLD = "The trail goes cold."
DEFAULT_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "..", "data",
                               "fixtures", "grid_replay.json")
TOKEN_ENV = "ESKOMSEPUSH_TOKEN"


def _cold() -> dict:
    return {"source": None, "content": None, "summary": COLD, "evidence": []}


def _load(fixture_path: Optional[str]) -> Optional[dict]:
    try:
        with open(fixture_path or DEFAULT_FIXTURE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _events(data: Optional[dict]) -> list:
    ev = (data or {}).get("events")
    return [e for e in ev if isinstance(e, dict)] if isinstance(ev, list) else []


def _outage_result(e: dict) -> dict:
    needed = ("id", "area", "stage", "start")
    if any(not e.get(k) for k in needed):
        return _cold()
    summary = f"Stage {e['stage']} for {e['area']} starts at {e['start']}."
    if e.get("end"):
        summary = summary[:-1] + f" and ends at {e['end']}."
    return {
        "source": e["id"],
        "content": json.dumps(e, sort_keys=True, ensure_ascii=False),
        "summary": summary,
        "evidence": [dict(e)],
    }


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
def _parse(iso) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(iso) if isinstance(iso, str) else None
    except ValueError:
        return None
def spoken_time(iso: str) -> Optional[str]:
    """`2026-11-30T18:00:00+02:00` -> `6 PM on 30 November 2026`. Uses the record's own
    local time (no UTC conversion). Minutes only when nonzero. None when unparseable."""
    d = _parse(iso)
    if d is None:
        return None
    hour = d.hour % 12 or 12
    clock = f"{hour}:{d.minute:02d}" if d.minute else str(hour)
    return f"{clock} {'AM' if d.hour < 12 else 'PM'} on {d.day} {_MONTHS[d.month - 1]} {d.year}"
def _start_key(e: dict) -> tuple:
    d = _parse(e["start"])
    if d is not None and d.tzinfo is not None:
        return (0, d.astimezone(timezone.utc), "")
    return (1, datetime.min.replace(tzinfo=timezone.utc), str(e["start"]))
def replay_outage(fixture_id: str, fixture_path: Optional[str] = None) -> dict:
    """Return one fixture event by id, or the cold trail."""
    for e in _events(_load(fixture_path)):
        if e.get("id") == fixture_id:
            return _outage_result(e)
    return _cold()


def current_stage(fixture_path: Optional[str] = None) -> dict:
    """Replay mode: the stage recorded in the fixture's status block."""
    status = (_load(fixture_path) or {}).get("status")
    if not isinstance(status, dict) or not status.get("stage"):
        return _cold()
    stage = status["stage"]
    summary = f"The current stage is {stage}."
    if status.get("stage_updated"):
        summary += f" Updated at {status['stage_updated']}."
    return {
        "source": "grid-replay-status",
        "content": json.dumps(status, sort_keys=True, ensure_ascii=False),
        "summary": summary,
        "evidence": [dict(status)],
    }


def next_outage(area: str, fixture_path: Optional[str] = None) -> dict:
    """Replay mode: the fixture event for `area` (case-insensitive exact name match)
    with the earliest start (parsed time; unparseable strings sort last). Unknown area gives the cold trail."""
    wanted = (area or "").strip().casefold()
    if not wanted:
        return _cold()
    hits = [e for e in _events(_load(fixture_path))
            if str(e.get("area", "")).casefold() == wanted and e.get("start")]
    if not hits:
        return _cold()
    return _outage_result(min(hits, key=_start_key))


def live_current_stage(http_get: Optional[Callable[..., dict]] = None,
                       enabled: bool = False) -> dict:
    """STUB. Live EskomSePush is OFF by default and its API shape is unverified, so this
    never sends a request. `http_get` is accepted for a future injectable client."""
    if not enabled or not os.environ.get(TOKEN_ENV):
        return _cold()
    return _cold()
