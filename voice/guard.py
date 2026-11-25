"""Spend guardrails: session limits and persistent daily/total session caps."""
from __future__ import annotations

import datetime
import fcntl
import json
import os
import tempfile
import threading
from dataclasses import dataclass


@dataclass
class Limits:
    max_session_seconds: float = 180
    idle_seconds: float = 60
    disconnect_grace: float = 5.0
    daily_cap: int = 20
    total_cap: int = 200

    def __post_init__(self):
        if self.max_session_seconds <= 0 or self.max_session_seconds > 480:
            raise ValueError("max_session_seconds must be above 0 and at most 480")
        if self.idle_seconds <= 0:
            raise ValueError("idle_seconds must be above 0")
        if self.disconnect_grace < 0:
            raise ValueError("disconnect_grace must not be negative")
        if self.daily_cap < 0 or self.total_cap < 0:
            raise ValueError("caps must not be negative")


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class _Corrupt(Exception):
    pass


class CapStore:
    """Persistent session counter. Fails closed when the state file cannot be read."""

    def __init__(self, path, limits: Limits | None = None, now=utcnow):
        self.path = str(path)
        self.limits = limits or Limits()
        self.now = now
        self._lock = threading.Lock()

    def _read(self, today: str) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return {"total": 0, "day": today, "day_count": 0}
        except (OSError, ValueError):
            raise _Corrupt()
        if not isinstance(raw, dict):
            raise _Corrupt()
        total, day, count = raw.get("total"), raw.get("day"), raw.get("day_count")
        for n in (total, count):
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                raise _Corrupt()
        if not isinstance(day, str):
            raise _Corrupt()
        if day != today:
            count = 0
        return {"total": total, "day": today, "day_count": count}

    def _write(self, state: dict) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".caps-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _locked(self, fn):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self._lock:
            with open(self.path + ".lock", "a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    return fn(self.now().strftime("%Y-%m-%d"))
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def try_acquire(self) -> tuple[bool, str | None]:
        def go(today):
            state = self._read(today)
            if state["total"] >= self.limits.total_cap:
                return False, "total_cap"
            if state["day_count"] >= self.limits.daily_cap:
                return False, "daily_cap"
            state["total"] += 1
            state["day_count"] += 1
            self._write(state)
            return True, None
        try:
            return self._locked(go)
        except (_Corrupt, OSError):
            return False, "total_cap"

    def snapshot(self) -> dict:
        """Remaining counts. Fails closed (zero remaining) when the file is unreadable."""
        def go(today):
            state = self._read(today)
            return {"daily_remaining": max(self.limits.daily_cap - state["day_count"], 0),
                    "total_remaining": max(self.limits.total_cap - state["total"], 0)}
        try:
            return self._locked(go)
        except (_Corrupt, OSError):
            return {"daily_remaining": 0, "total_remaining": 0}
