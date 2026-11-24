"""Python port of the rules in ui/protocol.js (see ui/EVENTS.md)."""
from __future__ import annotations

import re

STATES = ["idle", "listening", "speaking", "summoning", "shapeshifting", "alert"]
SPIRITS = ["enron", "wayback", "grid"]
ROLES = ["user", "spirit"]
KINDS = ["email", "snapshot", "grid"]
REQUIRED_META = {
    "email": ["evidence_id", "sender", "recipients", "date", "subject", "source_path"],
    "snapshot": ["url", "timestamp"],
    "grid": ["stage", "area", "start"],
}
MAX_TEXT = 2000000


def _scalar(v) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _meta_error(kind, meta, cold):
    if meta is None and cold:
        return None
    if not isinstance(meta, dict):
        return "metadata must be an object"
    for k, v in meta.items():
        if not (_scalar(v) or (isinstance(v, list) and all(_scalar(x) for x in v))):
            return f"metadata.{k} must be a scalar or array of scalars"
    if cold:
        return None
    for k in REQUIRED_META[kind]:
        if k not in meta:
            return f"metadata.{k} is required for kind {kind}"
    if kind == "snapshot" and not re.fullmatch(r"\d{14}", str(meta["timestamp"])):
        return "metadata.timestamp must be a 14-digit string"
    return None


def validate_event(ev) -> str | None:
    """Return None when the event is valid, else the error message."""
    if not isinstance(ev, dict):
        return "event must be a JSON object"
    t = ev.get("type")
    if not isinstance(t, str):
        return "type must be a string"
    if t == "transcript":
        if not isinstance(ev.get("turn_id"), str) or not ev["turn_id"]:
            return "turn_id must be a non-empty string"
        if ev.get("role") not in ROLES:
            return "role must be user or spirit"
        if not isinstance(ev.get("text"), str) or len(ev["text"]) > MAX_TEXT:
            return "text must be a string"
        if not isinstance(ev.get("final"), bool):
            return "final must be a boolean"
    elif t == "state":
        if ev.get("state") not in STATES:
            return "unknown state"
    elif t == "spirit":
        if ev.get("spirit") not in SPIRITS:
            return "unknown spirit"
    elif t == "appearance":
        if not isinstance(ev.get("config"), dict):
            return "config must be an object"
    elif t == "evidence":
        if ev.get("kind") not in KINDS:
            return "unknown evidence kind"
        source = ev.get("source", "<missing>")
        cold = source is None
        if not cold and (not isinstance(source, str) or not source):
            return "source must be a non-empty string or null"
        if cold:
            if ev.get("content", "<missing>") is not None:
                return "content must be null when source is null"
        elif not isinstance(ev.get("content"), str) or len(ev["content"]) > MAX_TEXT:
            return "content must be a string"
        if ev.get("excerpt") is not None and not isinstance(ev["excerpt"], str):
            return "excerpt must be a string"
        err = _meta_error(ev["kind"], ev.get("metadata"), cold)
        if err:
            return err
    elif t == "error":
        if not isinstance(ev.get("message"), str):
            return "message must be a string"
    else:
        return "unknown event type"
    return None
