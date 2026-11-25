"""Token usage metering and the append-only usage log. Records hold counts only, never text."""
from __future__ import annotations

import datetime
import json
import os
import uuid

_SIDES = ("input", "output")
_KINDS = ("speechTokens", "textTokens")


def _zero() -> dict:
    return {side: {kind: 0 for kind in _KINDS} for side in _SIDES}


def _valid_count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


class UsageMeter:
    """Keeps the latest cumulative `details.total` of every stream and sums them."""

    def __init__(self):
        self._streams: dict = {}

    def record(self, key, body) -> bool:
        """Store a usageEvent body for a stream. Malformed bodies are ignored (returns False)."""
        if not isinstance(body, dict):
            return False
        details = body.get("details")
        total = details.get("total") if isinstance(details, dict) else None
        if not isinstance(total, dict):
            return False
        parsed = _zero()
        for side in _SIDES:
            part = total.get(side)
            if part is None:
                continue
            if not isinstance(part, dict):
                return False
            for kind in _KINDS:
                if kind in part:
                    if not _valid_count(part[kind]):
                        return False
                    parsed[side][kind] = part[kind]
        current = self._streams.setdefault(key, _zero())
        for side in _SIDES:
            for kind in _KINDS:
                current[side][kind] = max(current[side][kind], parsed[side][kind])
        return True

    def totals(self) -> dict:
        out = _zero()
        for stream in self._streams.values():
            for side in _SIDES:
                for kind in _KINDS:
                    out[side][kind] += stream[side][kind]
        return out

    def streams(self) -> int:
        return len(self._streams)


class UsageLog:
    def __init__(self, path):
        self.path = str(path)

    def append(self, *, seconds: float, streams: int, end_reason: str, usage: dict,
               role: str = "public", now: datetime.datetime | None = None) -> dict:
        if role not in ("public", "judge"):
            raise ValueError("role must be public or judge")
        ts = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {"ts": ts, "session_id": uuid.uuid4().hex, "seconds": round(float(seconds), 1),
                  "streams": int(streams), "end_reason": str(end_reason), "role": role, "usage": usage}
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return record


def format_summary(record: dict) -> str:
    u = record["usage"]
    role = " role=judge" if record.get("role") == "judge" else ""
    return ("usage: session=%s reason=%s seconds=%s streams=%s in_speech=%s in_text=%s "
            "out_speech=%s out_text=%s" % (
                record["session_id"][:8], record["end_reason"], record["seconds"], record["streams"],
                u["input"]["speechTokens"], u["input"]["textTokens"],
                u["output"]["speechTokens"], u["output"]["textTokens"])) + role
