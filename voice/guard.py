"""Spend guardrails: session limits and persistent daily/total session caps."""
from __future__ import annotations

import datetime
import asyncio
import fcntl
import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
from collections import deque
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


MAX_CODE_CHARS = 256


class AccessGate:
    """Shared access code. Keeps only the sha256 digest, never the raw code."""

    def __init__(self, code: str | None, *, fail_limit: int = 5, window: float = 60.0,
                 global_limit: int = 50, delay: float = 1.0, clock=time.monotonic):
        self._digest = hashlib.sha256(code.encode()).digest() if code is not None else None
        self.fail_limit = fail_limit
        self.window = window
        self.global_limit = global_limit
        self.delay = delay
        self.clock = clock
        self._fails: dict[str, deque] = {}
        self._global: deque = deque()

    @property
    def required(self) -> bool:
        return self._digest is not None

    def check(self, candidate) -> bool:
        if self._digest is None:
            return True
        if not isinstance(candidate, str) or len(candidate) > MAX_CODE_CHARS:
            return False
        try:
            digest = hashlib.sha256(candidate.encode()).digest()
        except UnicodeEncodeError:  # lone surrogate, e.g. the JSON text "\\ud800": cannot be the code
            return False
        return hmac.compare_digest(digest, self._digest)

    def _prune(self, q: deque, now: float) -> None:
        while q and now - q[0] >= self.window:
            q.popleft()

    def locked(self, remote: str) -> bool:
        now = self.clock()
        self._prune(self._global, now)
        q = self._fails.get(remote)
        if q is not None:
            self._prune(q, now)
            if not q:
                del self._fails[remote]
        return len(self._global) >= self.global_limit or (q is not None and len(q) >= self.fail_limit)

    async def attempt(self, remote: str, candidate) -> str:
        """Returns 'ok', 'bad' or 'locked'. Locked attempts are not compared."""
        if self._digest is None:
            return "ok"
        if self.locked(remote):
            return "locked"
        if self.check(candidate):
            return "ok"
        now = self.clock()
        self._fails.setdefault(remote, deque()).append(now)
        self._global.append(now)
        if self.delay:
            await asyncio.sleep(self.delay)
        return "bad"
