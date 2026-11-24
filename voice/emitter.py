"""Builds EVENTS.md events, validates them, and fans them out to sinks and an optional recorder."""
from __future__ import annotations

import json
from typing import Callable

from voice.protocol_check import validate_event


class Emitter:
    def __init__(self, record_path: str | None = None):
        self.sinks: list[Callable[[dict], None]] = []
        self.events: list[dict] = []
        self.violations: list[str] = []
        self._record = open(record_path, "w", encoding="utf-8") if record_path else None

    def add_sink(self, sink: Callable[[dict], None]) -> None:
        self.sinks.append(sink)

    def emit(self, ev: dict) -> bool:
        """Validate and deliver. An invalid event is replaced by an error event, never sent."""
        err = validate_event(ev)
        if err:
            self.violations.append(f"{ev.get('type')}: {err}")
            ev = {"type": "error", "message": f"engine produced an invalid {ev.get('type')} event: {err}"}
        self.events.append(ev)
        if self._record:
            self._record.write(json.dumps(ev) + "\n")
            self._record.flush()
        for sink in list(self.sinks):
            try:
                sink(ev)
            except Exception:
                pass
        return err is None

    def transcript(self, turn_id: str, role: str, text: str, final: bool) -> bool:
        return self.emit({"type": "transcript", "turn_id": turn_id, "role": role,
                          "text": text, "final": final})

    def state(self, state: str) -> bool:
        return self.emit({"type": "state", "state": state})

    def spirit(self, spirit: str) -> bool:
        return self.emit({"type": "spirit", "spirit": spirit})

    def appearance(self, config: dict) -> bool:
        return self.emit({"type": "appearance", "config": config})

    def evidence(self, **fields) -> bool:
        return self.emit({"type": "evidence", **fields})

    def error(self, message: str) -> bool:
        return self.emit({"type": "error", "message": message})

    def close(self) -> None:
        if self._record:
            self._record.close()
            self._record = None
