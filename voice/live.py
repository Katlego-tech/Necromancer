"""LiveController: owns the lifecycle of billed voice sessions and enforces the guardrails.

One live session is one VoiceSession (single-use). The controller builds a fresh one per start,
counts it against the persistent caps, and stops it on session limit, idle timeout, client
disconnect or shutdown, always with one `paused` event, one usage log line and one printed summary.
"""
from __future__ import annotations

import asyncio
import time

from voice.guard import CapStore, Limits
from voice.usage import UsageLog, format_summary

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
                 video_url: str | None = None, autostart_on_connect: bool = True):
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

    def _emit_paused(self, reason: str) -> None:
        self.emitter.paused(reason, MESSAGES[reason], CAN_RESTART[reason], self.video_url or None)

    def status_event(self) -> dict | None:
        """The paused event describing why voice is off, or None while voice is live."""
        if self.state in ("starting", "live"):
            return None
        left = self.caps.snapshot()
        if left["total_remaining"] <= 0:
            return self.paused_event("total_cap")
        if left["daily_remaining"] <= 0:
            return self.paused_event("daily_cap")
        return self.paused_event(self.last_pause or "not_started")

    # ------------------------------------------------------------ proxies
    def feed_audio(self, pcm: bytes) -> None:
        if self.session is not None and self.state == "live":
            self.session.feed_audio(pcm)

    async def send_text(self, text: str) -> None:
        if self.session is None or self.state != "live":
            self._emit_status()
            return
        await self.session.send_text(text)

    async def summon(self, spirit_id: str) -> None:
        if self.session is None or self.state != "live":
            self._emit_status()
            return
        await self.session.summon(spirit_id)

    def _emit_status(self) -> None:
        ev = self.status_event()
        if ev is not None:
            self.emitter.paused(ev["reason"], ev["message"], ev["can_restart"], ev.get("video_url"))

    # ------------------------------------------------------------ lifecycle
    async def ensure_started(self) -> bool:
        async with self._lock:
            if self.state == "live":
                return True
            self.state = "starting"
            try:
                ok, reason = await asyncio.to_thread(self.caps.try_acquire)
                if not ok:
                    self.state = "off"
                    self._emit_paused(reason)
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
                        raise
                    self.emitter.error("live voice failed to start")
                    self.last_pause = "not_started"
                    self.state = "off"
                    self._emit_paused("not_started")
                    return False
                self.session = session
                self._started = self.clock()
                self.last_pause = None
                self.state = "live"
                self._watchdog = asyncio.create_task(self._watch(session, self._started))
                if self.clients == 0 and self._disc is None:
                    self._disc = asyncio.create_task(self._disconnect_timer())
                return True
            finally:
                if self.state == "starting":
                    self.state = "off"

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
            seconds = self.clock() - self._started
            self._emit_paused(reason)
            try:
                await asyncio.wait_for(session.close(), CLOSE_TIMEOUT)
            except Exception:
                self.emitter.error("live voice did not close cleanly")
            try:
                record = self.usage_log.append(seconds=seconds, streams=session.usage.streams(),
                                               end_reason=reason, usage=session.usage.totals())
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

    async def _watch(self, session, started: float) -> None:
        while True:
            await asyncio.sleep(self.tick)
            now = self.clock()
            if now - started >= self.limits.max_session_seconds:
                self._spawn_stop("session_limit")
                return
            if now - max(session.last_activity, started) >= self.limits.idle_seconds:
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
