"""Grid Ghost spirit data: persona, tool definitions, evidence mapping and alert builder.
The persona names no colours, costumes or props. Appearance comes from
mascot/costumes/grid.json through the engine. Alerts only: nothing here controls or
suggests controlling devices.
"""
from __future__ import annotations
from typing import Optional
from spirits.grid import tools as grid_tools
from voice.summon import Spirit, ToolDef
COLD = grid_tools.COLD
VOICE_ID = "tiffany"
DEFAULT_AREA = "Sandton (Demo Area 1)"
REPLAY_SENTENCE = "This is a replay of synthetic demo data."
REPLAY_LABEL = "Replay of synthetic demo data. Not a live alert."
NOT_IN_STATUS = "not in status record"
PERSONA = f"""You are the Grid Ghost, the voice of the power grid schedule. You warn; you do not chat.
Rules you never break:
1. Speak first only when the system sends you an alert. Otherwise wait for the user.
2. In an alert, state the stage, the area and the start time exactly as given to you, then say: {REPLAY_SENTENCE}
3. Every schedule value you state comes from a tool result or from the alert text. Say it as given. Never round, shift or guess a time, stage or area.
4. Say the replay sentence whenever you state a schedule value: all of this data is a replay of synthetic demo data.
5. Answer in at most two short spoken sentences.
6. If a tool returns the summary "{COLD}", say exactly "{COLD}" and nothing else.
7. Never mention, suggest or offer to control any device or appliance. You only report the schedule.
"""
def to_evidence(result: dict) -> dict:
    """Map a tool result dict to the fields of an `evidence` UI event."""
    if result.get("source") is None:
        return {"kind": "grid", "source": None, "content": None}
    first = (result.get("evidence") or [{}])[0]
    meta = {"stage": first.get("stage"),
            "area": first.get("area") or NOT_IN_STATUS,
            "start": first.get("start") or NOT_IN_STATUS}
    for k in ("end", "stage_updated"):
        if first.get(k):
            meta[k] = first[k]
    meta["mode"] = "replay"
    meta["label"] = REPLAY_LABEL
    return {"kind": "grid", "source": result["source"], "content": result["content"],
            "metadata": meta}
def _speakable(result: dict) -> dict:
    """Copy of an outage result whose summary uses spoken times. content/evidence untouched."""
    ev = (result.get("evidence") or [None])[0]
    if result.get("source") is None or not ev or "area" not in ev:
        return result
    start = grid_tools.spoken_time(ev.get("start"))
    if start is None:
        return result
    summary = f"Stage {ev['stage']} for {ev['area']} starts at {start}"
    if ev.get("end"):
        end = grid_tools.spoken_time(ev["end"])
        summary += f" and ends at {end}" if end else f" and ends at {ev['end']}"
    return {**result, "summary": summary + "."}
def make_tools(fixture_path: Optional[str] = None) -> tuple:
    def nxt(args: dict) -> dict:
        return _speakable(grid_tools.next_outage(args.get("area", ""), fixture_path))
    def stage(args: dict) -> dict:
        return grid_tools.current_stage(fixture_path)
    def replay(args: dict) -> dict:
        return _speakable(grid_tools.replay_outage(args.get("fixture_id", ""), fixture_path))
    return (
        ToolDef("next_outage", "Get the next scheduled outage for one named area from the replay schedule.",
                {"type": "object", "properties": {
                    "area": {"type": "string", "description": "exact area name"}},
                 "required": ["area"]}, nxt, to_evidence),
        ToolDef("current_stage", "Get the current stage recorded in the replay schedule.",
                {"type": "object", "properties": {}}, stage, to_evidence),
        ToolDef("replay_outage", "Read one recorded outage event by its fixture id.",
                {"type": "object", "properties": {
                    "fixture_id": {"type": "string", "description": "fixture event id"}},
                 "required": ["fixture_id"]}, replay, to_evidence),
    )
def make_spirit(fixture_path: Optional[str] = None) -> Spirit:
    return Spirit("grid", PERSONA, VOICE_ID, make_tools(fixture_path),
                  "mascot/costumes/grid.json")
def build_alert(area: Optional[str] = None, fixture_path: Optional[str] = None) -> Optional[dict]:
    """Build the alert from the schedule record. None when the trail is cold or the
    start time cannot be spoken."""
    result = grid_tools.next_outage(area or DEFAULT_AREA, fixture_path)
    if result.get("source") is None:
        return None
    ev = (result.get("evidence") or [{}])[0]
    spoken = grid_tools.spoken_time(ev.get("start"))
    if spoken is None:
        return None
    stage, name = str(ev["stage"]), ev["area"]
    prompt = ("ALERT FROM THE SCHEDULE SYSTEM, not from the user. Warn the listener now, out loud, "
              f"and say these three values exactly as written: stage {stage}; area {name}; "
              f"start time {spoken}. Begin with the word Warning. Then say: {REPLAY_SENTENCE} "
              "Add nothing else.")
    line = (f"Warning. Stage {stage}; area {name}; start time {spoken}. "
            f"{REPLAY_SENTENCE} (not spoken)")
    return {"prompt": prompt, "evidence": to_evidence(result), "stage": stage, "area": name,
            "spoken_start": spoken, "line": line}
