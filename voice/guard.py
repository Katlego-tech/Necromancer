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
    # Judge allowance (T7.10). Placeholders until T7.5 measures a real session.
    judge_max_session_seconds: float = 300
    judge_idle_seconds: float = 90
    judge_daily_cap: int = 30
    judge_total_cap: int = 150
    def session_seconds(self, role: str) -> float:
        return self.judge_max_session_seconds if role == "judge" else self.max_session_seconds
    def idle_limit(self, role: str) -> float:
        return self.judge_idle_seconds if role == "judge" else self.idle_seconds
    def caps_for(self, role: str) -> tuple[int, int]:
        """(daily, total) for a role."""
        return (self.judge_daily_cap, self.judge_total_cap) if role == "judge" else (self.daily_cap, self.total_cap)

    def __post_init__(self):
        if self.max_session_seconds <= 0 or self.max_session_seconds > 480:
            raise ValueError("max_session_seconds must be above 0 and at most 480")
        if self.idle_seconds <= 0:
            raise ValueError("idle_seconds must be above 0")
        if self.disconnect_grace < 0:
            raise ValueError("disconnect_grace must not be negative")
        if self.daily_cap < 0 or self.total_cap < 0 or self.judge_daily_cap < 0 or self.judge_total_cap < 0:
            raise ValueError("caps must not be negative")
        if self.judge_max_session_seconds <= 0 or self.judge_max_session_seconds > 480:
            raise ValueError("judge_max_session_seconds must be above 0 and at most 480")
        if self.judge_idle_seconds <= 0:
            raise ValueError("judge_idle_seconds must be above 0")


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
            return {"total": 0, "day": today, "day_count": 0, "judge_total": 0, "judge_day_count": 0}
        except (OSError, ValueError):
            raise _Corrupt()
        if not isinstance(raw, dict):
            raise _Corrupt()
        total, day, count = raw.get("total"), raw.get("day"), raw.get("day_count")
        # Files written before judge access have no judge counters: they start at zero.
        jtotal, jcount = raw.get("judge_total", 0), raw.get("judge_day_count", 0)
        for n in (total, count, jtotal, jcount):
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                raise _Corrupt()
        if not isinstance(day, str):
            raise _Corrupt()
        if day != today:
            count = jcount = 0
        return {"total": total, "day": today, "day_count": count,
                "judge_total": jtotal, "judge_day_count": jcount}

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

    @staticmethod
    def _keys(role: str) -> tuple[str, str]:
        return ("judge_total", "judge_day_count") if role == "judge" else ("total", "day_count")
    def try_acquire(self, role: str = "public") -> tuple[bool, str | None]:
        """Take one session slot from the role's own counters. Roles never share a cap."""
        tkey, dkey = self._keys(role)
        daily_cap, total_cap = self.limits.caps_for(role)
        def go(today):
            state = self._read(today)
            if state[tkey] >= total_cap:
                return False, "total_cap"
            if state[dkey] >= daily_cap:
                return False, "daily_cap"
            state[tkey] += 1
            state[dkey] += 1
            self._write(state)
            return True, None
        try:
            return self._locked(go)
        except (_Corrupt, OSError):
            return False, "total_cap"

    def snapshot(self, role: str = "public") -> dict:
        """Remaining counts. Fails closed (zero remaining) when the file is unreadable."""
        tkey, dkey = self._keys(role)
        daily_cap, total_cap = self.limits.caps_for(role)
        def go(today):
            state = self._read(today)
            return {"daily_remaining": max(daily_cap - state[dkey], 0),
                    "total_remaining": max(total_cap - state[tkey], 0)}
        try:
            return self._locked(go)
        except (_Corrupt, OSError):
            return {"daily_remaining": 0, "total_remaining": 0}


MAX_CODE_CHARS = 256
MIN_JUDGE_CODE_CHARS = 12
# End of 29 Jan 2027 Pacific Standard Time (UTC-8). Judging ends 1:00 pm PT that day.
DEFAULT_JUDGE_EXPIRES = "2027-01-29T23:59:59-08:00"
ROLES = ("public", "judge")
def parse_expiry(raw: str | None) -> datetime.datetime | None:
    """ISO 8601 date and time WITH a UTC offset (e.g. 2027-01-29T23:59:59-08:00), or 'none' for no expiry."""
    text = (raw or "").strip() or DEFAULT_JUDGE_EXPIRES
    if text.lower() == "none":
        return None
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("NECRO_JUDGE_EXPIRES must be an ISO 8601 date and time with a UTC offset, or 'none'")
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("NECRO_JUDGE_EXPIRES must include a UTC offset, for example -08:00")
    return when
def _digest(text: str) -> bytes | None:
    try:
        return hashlib.sha256(text.encode()).digest()
    except UnicodeEncodeError:  # lone surrogate, e.g. the JSON text "\\ud800": cannot be a code
        return None
class AccessGate:
    """Public and judge access codes. Keeps only sha256 digests, never a raw code.
    One attempt compares the candidate against both digests (no early exit) and charges one shared
    failure budget, so probing both codes costs the same as probing one.
    Emergency switches (see `allowed`) make a role's code stop matching without a redeploy."""
    def __init__(self, code: str | None, *, judge_code: str | None = None, judge_expires=None,
                 control_dir: str | None = None, disabled=(), now=utcnow,
                 fail_limit: int = 5, window: float = 60.0,
                 global_limit: int = 50, delay: float = 1.0, clock=time.monotonic):
        if judge_code is not None:
            if len(judge_code.strip()) < MIN_JUDGE_CODE_CHARS:
                raise ValueError(f"NECRO_JUDGE_CODE must be at least {MIN_JUDGE_CODE_CHARS} characters")
            if code is not None and judge_code.strip() == code.strip():
                raise ValueError("NECRO_JUDGE_CODE must differ from NECRO_ACCESS_CODE")
        self._digest = _digest(code) if code is not None else None
        self._judge_digest = _digest(judge_code) if judge_code is not None else None
        self.judge_expires = judge_expires
        self.control_dir = control_dir
        self.disabled = frozenset(disabled)
        self.now = now
        self.fail_limit = fail_limit
        self.window = window
        self.global_limit = global_limit
        self.delay = delay
        self.clock = clock
        self._fails: dict[str, deque] = {}
        self._global: deque = deque()
    @property
    def required(self) -> bool:
        return self._digest is not None or self._judge_digest is not None
    @property
    def judge_enabled(self) -> bool:
        return self._judge_digest is not None
    def allowed(self, role: str) -> bool:
        """False when the role's code is switched off (startup flag or a `disable-<role>` file in the
        control directory, read on every call so it works on a running server) or the judge code expired."""
        if role in self.disabled:
            return False
        if self.control_dir:
            try:
                # os.path.exists would swallow a permission error and report "not there", i.e. fail
                # OPEN. A missing file or directory means "not switched off"; any other error means
                # we cannot tell, so the role is treated as switched off.
                os.stat(os.path.join(self.control_dir, "disable-" + role))
                return False
            except (FileNotFoundError, NotADirectoryError):
                pass
            except (OSError, ValueError):
                return False  # cannot tell: fail closed
        if role == "judge" and self.judge_expires is not None and self.now() >= self.judge_expires:
            return False
        return True
    def role_of(self, candidate) -> str | None:
        """'public', 'judge' or None. Both digests are always compared, then switched-off roles are masked."""
        if not isinstance(candidate, str) or len(candidate) > MAX_CODE_CHARS:
            return None
        digest = _digest(candidate)
        if digest is None:
            return None
        pub = self._digest is not None and hmac.compare_digest(digest, self._digest)
        jud = self._judge_digest is not None and hmac.compare_digest(digest, self._judge_digest)
        if jud and self.allowed("judge"):
            return "judge"
        if pub and self.allowed("public"):
            return "public"
        return None
    def check(self, candidate) -> bool:
        if not self.required:
            return True
        return self.role_of(candidate) is not None
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
    async def attempt_role(self, remote: str, candidate) -> tuple[str, str | None]:
        """Returns ('ok', role), ('bad', None) or ('locked', None). Locked attempts are not compared."""
        if not self.required:
            return "ok", "public"
        if self.locked(remote):
            return "locked", None
        role = self.role_of(candidate)
        if role is not None:
            return "ok", role
        now = self.clock()
        self._fails.setdefault(remote, deque()).append(now)
        self._global.append(now)
        if self.delay:
            await asyncio.sleep(self.delay)
        return "bad", None
    async def attempt(self, remote: str, candidate) -> str:
        """Returns 'ok', 'bad' or 'locked'."""
        return (await self.attempt_role(remote, candidate))[0]
