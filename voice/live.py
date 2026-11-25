"""LiveController: owns the lifecycle of billed voice sessions and enforces the guardrails.

One live session is one VoiceSession (single-use). The controller builds a fresh one per start,
counts it against the persistent caps, and stops it on session limit, idle timeout, client
disconnect or shutdown, always with one `paused` event, one usage log line and one printed summary.
"""
from __future__ import annotations

import asyncio
import time

from voice.guard import CapStore, Limits
from voice.usage import UsageLog, UsageMeter, format_summary

MESSAGES = {
    "daily_cap": "Today's live voice sessions are used up. Come back tomorrow.",
    "total_cap": "All live voice sessions for this demo are used up.",
    "session_limit": "The live session reached its time limit.",
    "idle_timeout": "The live session ended after a period of silence.",
    "bad_code": "That access code was not accepted.",
    "code_required": "An access code is required to start live voice.",
    "disconnected": "The live session ended because the browser disconnected.",
    "shutdown": "The server is shutting down.",
    "not_started": "Live voice is not running. Start it to talk to a spirit.",
}
CAN_RESTART = {
    "daily_cap": False, "total_cap": False, "session_limit": True, "idle_timeout": True,
    "bad_code": True, "code_required": True, "disconnected": True, "shutdown": False,
    "not_started": True,
}
CLOSE_TIMEOUT = 15.0


class LiveController:
    def __init__(self, make_session, emitter, limits: Limits, caps: CapStore, usage_log: UsageLog, *,
                 factory=None, clock=time.monotonic, print_fn=print, tick: float = 1.0,
                 video_url: str | None = None, autostart_on_connect: bool = True, registry=None,
                 role_allowed=None):
        self.make_session = make_session
        self.emitter = emitter
        self.limits = limits
        self.caps = caps
        self.usage_log = usage_log
        self.factory = factory
        self.clock = clock
        self.print_fn = print_fn
        self.tick = tick
        self.video_url = video_url
        self.autostart_on_connect = autostart_on_connect
        self.registry = registry                # only used to show an alert without a live session
        self.role_allowed = role_allowed or (lambda role: True)   # emergency switch and expiry
        self.role = "public"                    # role that opened the live session
        self._alert_n = 0
        self.audio_sinks: list = []
        self.clear_sinks: list = []
        self.state = "off"                      # off | starting | live | ending
        self.session = None
        self.last_pause: str | None = None
        self.clients = 0
        self._lock = asyncio.Lock()
        self._started = 0.0
        self._watchdog: asyncio.Task | None = None
        self._disc: asyncio.Task | None = None
        self._stops: set[asyncio.Task] = set()

    # ------------------------------------------------------------ events
    def paused_event(self, reason: str) -> dict:
        ev = {"type": "paused", "reason": reason, "message": MESSAGES[reason],
              "can_restart": CAN_RESTART[reason]}
        if self.video_url:
            ev["video_url"] = self.video_url
        return ev

    def _emit_paused(self, reason: str, audience: str | None = None) -> None:
        self.emitter.paused(reason, MESSAGES[reason], CAN_RESTART[reason], self.video_url or None, audience)

    def status_event(self, role: str = "public") -> dict | None:
        """The paused event describing why voice is off for this role, or None while voice is live."""
        if self.state in ("starting", "live"):
            return None
        if not self.role_allowed(role):
            return self.paused_event("bad_code")
        left = self.caps.snapshot(role)
        if left["total_remaining"] <= 0:
            return self.paused_event("total_cap")
        if left["daily_remaining"] <= 0:
            return self.paused_event("daily_cap")
        return self.paused_event(self.last_pause or "not_started")

    # ------------------------------------------------------------ proxies
    def feed_audio(self, pcm: bytes) -> None:
        if self.session is not None and self.state == "live":
            self.session.feed_audio(pcm)

    async def send_text(self, text: str, role: str = "public") -> None:
        if self.session is None or self.state != "live":
            self._emit_status(role)
            return
        await self.session.send_text(text)

    async def summon(self, spirit_id: str, role: str = "public") -> None:
        if self.session is None or self.state != "live":
            self._emit_status(role)
            return
        await self.session.summon(spirit_id)

    async def alert(self, spirit_id: str, prompt: str, *, evidence: dict | None = None,
                    expected: tuple | None = None, line: str | None = None) -> str:
        """Live: the session speaks it ("started" or "busy"). Otherwise the alert is shown only
        (spirit, appearance, evidence, scripted line): no session, no cap, no state event."""
        if self.session is not None and self.state == "live":
            return await self.session.alert(spirit_id, prompt, evidence=evidence, expected=expected, line=line)
        if self.registry is None or spirit_id not in self.registry:
            return "unknown_spirit"
        spirit = self.registry.get(spirit_id)
        self.emitter.spirit(spirit.id)
        if spirit.appearance_path:
            from pathlib import Path
            from mascot.appearance import load_costume
            from voice.session import REPO_ROOT
            path = Path(spirit.appearance_path)
            self.emitter.appearance(load_costume(str(path if path.is_absolute() else REPO_ROOT / path)))
        if evidence:
            self.emitter.evidence(**evidence)
        if line:
            self._alert_n += 1
            self.emitter.transcript(f"alert{self._alert_n}", "spirit", line, True)
        return "visual"

    def _emit_status(self, role: str = "public") -> None:
        ev = self.status_event(role)
        if ev is not None:
            self.emitter.paused(ev["reason"], ev["message"], ev["can_restart"], ev.get("video_url"), role)

    # ------------------------------------------------------------ lifecycle
    async def ensure_started(self, role: str = "public") -> bool:
        """Start a live session billed to `role`'s caps. If one is already live, join it (no new slot)."""
        async with self._lock:
            if self.state == "live":
                return True
            if not self.role_allowed(role):
                self._emit_paused("bad_code", role)
                return False
            self.state = "starting"
            try:
                acquire = asyncio.ensure_future(asyncio.to_thread(self.caps.try_acquire, role))
                try:
                    ok, reason = await asyncio.shield(acquire)
                except asyncio.CancelledError:
                    # The thread keeps running and may still take a slot. Account for it.
                    got = await asyncio.gather(acquire, return_exceptions=True)
                    if isinstance(got[0], tuple) and got[0][0]:
                        self._log_aborted_start(role=role)
                    raise
                if not ok:
                    self.state = "off"
                    self._emit_paused(reason, role)
                    return False
                self.emitter.events.clear()
                session = self.make_session()
                session.audio_sinks = self.audio_sinks
                session.clear_sinks = self.clear_sinks
                try:
                    await session.start()
                except BaseException as e:
                    try:
                        await asyncio.wait_for(session.close(), CLOSE_TIMEOUT)
                    except Exception:
                        pass
                    if isinstance(e, asyncio.CancelledError):
                        self._log_aborted_start(session, role)
                        raise
                    self.emitter.error("live voice failed to start")
                    self.last_pause = "not_started"
                    self.state = "off"
                    self._emit_paused("not_started")
                    return False
                self.session = session
                self.role = role
                self._started = self.clock()
                self.last_pause = None
                self.state = "live"
                self._watchdog = asyncio.create_task(self._watch(session, self._started, role))
                if self.clients == 0 and self._disc is None:
                    self._disc = asyncio.create_task(self._disconnect_timer())
                return True
            finally:
                if self.state == "starting":
                    self.state = "off"

    def _log_aborted_start(self, session=None, role: str = "public") -> None:
        """A start cancelled after taking a cap slot keeps the slot (a Bedrock stream may have
        been opened and billed) but must leave a usage line, so the slot is accounted for."""
        try:
            usage = session.usage if session is not None else None
            record = self.usage_log.append(
                seconds=0, streams=usage.streams() if usage else 0, end_reason="start_cancelled",
                usage=(usage or UsageMeter()).totals(), role=role)
            self.print_fn(format_summary(record))
        except Exception:
            self.emitter.error("usage log could not be written")

    async def stop(self, reason: str) -> None:
        async with self._lock:
            if self.state != "live" or self.session is None:
                return
            self.state = "ending"
            me = asyncio.current_task()
            for task in (self._watchdog, self._disc):
                if task is not None and task is not me:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self._watchdog = None
            self._disc = None
            session, self.session = self.session, None
            role = self.role
            seconds = self.clock() - self._started
            self._emit_paused(reason)
            try:
                await asyncio.wait_for(session.close(), CLOSE_TIMEOUT)
            except Exception:
                self.emitter.error("live voice did not close cleanly")
            try:
                record = self.usage_log.append(seconds=seconds, streams=session.usage.streams(),
                                               end_reason=reason, usage=session.usage.totals(), role=role)
                self.print_fn(format_summary(record))
            except Exception:
                self.emitter.error("usage log could not be written")
            self.last_pause = reason
            self.state = "off"

    async def shutdown(self) -> None:
        await self.stop("shutdown")
        if self._disc is not None:
            self._disc.cancel()
            await asyncio.gather(self._disc, return_exceptions=True)
            self._disc = None
        if self._stops:
            await asyncio.gather(*list(self._stops), return_exceptions=True)

    # ------------------------------------------------------------ watchdog
    def _spawn_stop(self, reason: str) -> None:
        task = asyncio.create_task(self.stop(reason))
        self._stops.add(task)
        task.add_done_callback(self._stops.discard)

    async def _watch(self, session, started: float, role: str = "public") -> None:
        while True:
            await asyncio.sleep(self.tick)
            now = self.clock()
            if not self.role_allowed(role):  # code switched off or expired while the session runs
                self._spawn_stop("bad_code")
                return
            if now - started >= self.limits.session_seconds(role):
                self._spawn_stop("session_limit")
                return
            if now - max(session.last_activity, started) >= self.limits.idle_limit(role):
                self._spawn_stop("idle_timeout")
                return

    # ------------------------------------------------------------ clients
    def client_joined(self) -> None:
        self.clients += 1
        # Only while live: once stop() has begun, _disc may be the task running it.
        if self._disc is not None and self.state == "live":
            self._disc.cancel()
            self._disc = None

    def client_left(self) -> None:
        self.clients = max(self.clients - 1, 0)
        if self.clients == 0 and self.state == "live" and self._disc is None:
            self._disc = asyncio.create_task(self._disconnect_timer())

    async def _disconnect_timer(self) -> None:
        await asyncio.sleep(self.limits.disconnect_grace)
        await self.stop("disconnected")
