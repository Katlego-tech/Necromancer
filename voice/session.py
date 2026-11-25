"""VoiceSession: one conversation with a Nova Sonic stream, owned by the engine.

The engine session (transcript, emitter, mic queue, counters) outlives any single Bedrock
stream. Summon and the 8-minute renewal swap the Bedrock stream underneath it ("handover").
This module imports no AWS SDK and knows nothing about concrete spirits.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import itertools
import json
import time
import uuid
from pathlib import Path

from voice import events
from voice.emitter import Emitter
from voice.grounding_check import check_turn, is_cold
from voice.summon import SUMMON_TOOL, DEFAULT_REGISTRY, Registry, Spirit, summon_tool_def
from voice.usage import UsageMeter

MAX_TOOL_CONTENT = 40000
FILLER_TEXT = "Tell the user in one short sentence that you are still searching."
SILENCE = bytes(1024 * 2)  # 1024 samples of 16 kHz PCM16, about 64 ms
REPO_ROOT = Path(__file__).resolve().parent.parent


class _ConnClosed(Exception):
    pass


_conn_counter = itertools.count(1)


class _Conn:
    """One Bedrock stream plus the state that belongs to it."""

    def __init__(self, stream, spirit: Spirit, tools: dict, started: float):
        self.stream = stream
        self.spirit = spirit
        self.tools = tools
        self.started = started
        self.key = next(_conn_counter)         # unique per stream, used by the usage meter
        self.prompt = str(uuid.uuid4())
        self.audio_name = str(uuid.uuid4())
        self.lock = asyncio.Lock()
        self.closed = False
        self.send_failed = False
        self.blocks: dict[str, dict] = {}
        self.pending: set[asyncio.Task] = set()
        self.reader: asyncio.Task | None = None


class VoiceSession:
    def __init__(self, registry: Registry | None, spirit_id: str, factory, emitter: Emitter, *,
                 clock=time.monotonic, frame_seconds: float = 0.064, tool_timeout: float = 20.0,
                 filler_after: float = 1.5, renew_after: float = 420.0,
                 close_cap: float = 10.0, history_cap: int = 200_000,
                 summon_grace: float = 5.0, turn_grace: float = 0.0,
                 reader_grace: float = 2.0):
        self.registry = registry or DEFAULT_REGISTRY
        self.factory = factory
        self.emitter = emitter
        self.clock = clock
        self.frame_seconds = frame_seconds
        self.tool_timeout = tool_timeout
        self.filler_after = filler_after
        self.renew_after = renew_after
        self.close_cap = close_cap
        self.history_cap = history_cap
        self.summon_grace = summon_grace
        self.turn_grace = turn_grace            # wait this long after an END_TURN text block
        self.reader_grace = reader_grace        # wait this long for the last usageEvent after sessionEnd
        self.usage = UsageMeter()
        self.last_activity = clock()
        self.spirit = self.registry.get(spirit_id)
        self.transcript: list[dict] = []        # {"role": "user"|"spirit", "text": str}, FINAL only
        self.tool_results: list[dict] = []      # raw tool dicts, as returned by handlers
        self.tool_log: list[dict] = []          # {"name","args","result","payload"}
        self.grounding_violations: list[str] = []
        self.audio_bytes = 0
        self.audio_sinks: list = []             # callables(bytes), PCM16 24 kHz
        self.clear_sinks: list = []             # callables(), called on barge-in
        self.state = ""  # nothing emitted yet
        self._active: _Conn | None = None
        self._mic: collections.deque = collections.deque(maxlen=200)
        self._turn: dict | None = None
        self._user_n = 0
        self._turn_n = 0
        self._cold = False
        self._turn_timer: asyncio.Task | None = None
        self._spec_blocks = 0                   # SPECULATIVE assistant text blocks in this turn
        self._final_blocks = 0                  # their FINAL echoes seen so far
        self._barged = False
        self._closing = False
        self._renewal = False
        self._handover_active = False
        self._handover_task: asyncio.Task | None = None
        self._pending: tuple | None = None      # (spirit_id, since, grace)
        self._bg: set[asyncio.Task] = set()
        self._pump_task: asyncio.Task | None = None

    # ------------------------------------------------------------ public API
    async def start(self) -> None:
        self._emit_identity(self.spirit)
        self._set_state("idle", force=True)
        self._active = await self._open_conn(self.spirit, self._history())
        self._set_state("listening", force=True)
        self._pump_task = asyncio.create_task(self._pump())

    def _touch(self) -> None:
        self.last_activity = self.clock()

    def feed_audio(self, pcm: bytes) -> None:
        self._mic.append(pcm)

    async def inject_text(self, text: str) -> None:
        """Send cross-modal USER text without recording it in the transcript."""
        conn = self._active
        if conn is None:
            raise RuntimeError("session not started")
        await self._send(conn, *events.text_block(conn.prompt, str(uuid.uuid4()), "USER", text, True))

    async def send_text(self, text: str) -> None:
        """A typed user turn: shown in the transcript, then sent to the model."""
        if self._turn_timer:
            self._close_turn()
        self._touch()
        self._user_n += 1
        self._barged = False
        self.emitter.transcript(f"u{self._user_n}", "user", text, True)
        self.transcript.append({"role": "user", "text": text})
        await self.inject_text(text)

    async def summon(self, spirit_id: str) -> None:
        """Hand over to another spirit when the current spirit turn ends."""
        if spirit_id not in self.registry:
            self.emitter.error(f"unknown spirit: {spirit_id}")
            return
        self._pending = (spirit_id, self.clock(), 0.0)
        self._maybe_start_pending()
        if self._handover_task:
            await self._handover_task

    async def drain(self) -> None:
        """Wait for handover and background close tasks (tests, shutdown)."""
        while self._handover_task or self._bg:
            tasks = [t for t in ([self._handover_task] if self._handover_task else []) + list(self._bg)]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        self._closing = True
        if self._turn is not None:
            self._close_turn()
        if self._pump_task:
            self._pump_task.cancel()
            await asyncio.gather(self._pump_task, return_exceptions=True)
        ht = self._handover_task
        if ht is not None:
            ht.cancel()
            await asyncio.gather(ht, return_exceptions=True)
            self._handover_task = None
            self._handover_active = False
        await self.drain()
        if self._active:
            await self._close_conn(self._active)
        self._active = None
        self._set_state("idle", force=True)

    # ------------------------------------------------------------ state
    def _set_state(self, state: str, force: bool = False) -> None:
        if self._handover_active and not force:
            return
        if state != self.state:
            self.state = state
            self.emitter.state(state)

    def _emit_identity(self, spirit: Spirit) -> None:
        self.emitter.spirit(spirit.id)
        if spirit.appearance_path:
            from mascot.appearance import load_costume
            path = Path(spirit.appearance_path)
            if not path.is_absolute():
                path = REPO_ROOT / path
            self.emitter.appearance(load_costume(str(path)))

    # ------------------------------------------------------------ connection
    def _tool_defs(self, spirit: Spirit) -> dict:
        tools = {t.name: t for t in spirit.tools}
        if len(self.registry.ids()) > 1:
            tools[SUMMON_TOOL] = summon_tool_def(self.registry)
        return tools

    def _history(self) -> list[dict]:
        merged: list[dict] = []
        for t in self.transcript:
            role = "USER" if t["role"] == "user" else "ASSISTANT"
            if merged and merged[-1]["role"] == role:
                merged[-1]["text"] += " " + t["text"]
            else:
                merged.append({"role": role, "text": t["text"]})
        while merged and merged[0]["role"] != "USER":
            merged.pop(0)
        while merged and sum(len(m["text"]) for m in merged) > self.history_cap:
            merged.pop(0)
            while merged and merged[0]["role"] != "USER":
                merged.pop(0)
        return merged

    async def _open_conn(self, spirit: Spirit, history: list[dict]) -> _Conn:
        stream = await self.factory.open()
        tools = self._tool_defs(spirit)
        conn = _Conn(stream, spirit, tools, self.clock())
        try:
            await self._send(conn, events.session_start())
            await self._send(conn, events.prompt_start(
                conn.prompt, spirit.voice_id, [t.spec() for t in tools.values()]))
            await self._send(conn, *events.text_block(
                conn.prompt, str(uuid.uuid4()), "SYSTEM", spirit.persona_prompt, False))
            if history:
                await self._send(conn, *events.history_blocks(
                    conn.prompt, history, lambda: str(uuid.uuid4())))
            await self._send(conn, events.audio_start(conn.prompt, conn.audio_name))
        except BaseException:
            conn.closed = True
            await stream.close()
            raise
        conn.reader = asyncio.create_task(self._read(conn))
        return conn

    async def _send(self, conn: _Conn, *evs: dict) -> None:
        async with conn.lock:
            if conn.closed:
                raise _ConnClosed()
            for ev in evs:
                await conn.stream.send(ev)

    async def _close_conn(self, conn: _Conn) -> None:
        if conn.pending:
            await asyncio.wait(set(conn.pending), timeout=self.close_cap)
        async with conn.lock:
            if conn.closed:
                return
            try:
                for ev in (events.audio_end(conn.prompt, conn.audio_name),
                           events.prompt_end(conn.prompt), events.session_end()):
                    await conn.stream.send(ev)
            except Exception:
                pass
            conn.closed = True
        await conn.stream.close()
        if conn.reader and conn.reader is not asyncio.current_task():
            if self.reader_grace > 0:
                await asyncio.wait({conn.reader}, timeout=self.reader_grace)
            conn.reader.cancel()
            await asyncio.gather(conn.reader, return_exceptions=True)

    def _spawn_bg(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # ------------------------------------------------------------ audio pump
    async def _pump(self) -> None:
        while not self._closing:
            conn = self._active
            if conn is not None and not conn.closed:
                real = self._mic.popleft() if self._mic else None
                try:
                    await self._send(conn, events.audio_input(
                        conn.prompt, conn.audio_name, real if real is not None else SILENCE))
                except _ConnClosed:
                    if real is not None:
                        self._mic.appendleft(real)
                except Exception as e:
                    if not conn.send_failed:
                        conn.send_failed = True
                        self.emitter.error(f"audio send failed: {type(e).__name__}")
                self._check_renewal(conn)
                self._maybe_start_pending()
            await asyncio.sleep(self.frame_seconds)

    def _check_renewal(self, conn: _Conn) -> None:
        if (self.clock() - conn.started >= self.renew_after and self._pending is None
                and not self._handover_active and self._turn is None and self.state != "speaking"):
            self._pending = (conn.spirit.id, self.clock(), 0.0)
            self._renewal = True

    def _maybe_start_pending(self, after_turn: bool = False) -> None:
        if self._pending is None or self._handover_active or self._turn is not None:
            return
        if self.state == "speaking":
            return
        spirit_id, since, grace = self._pending
        if not after_turn and self.clock() - since < grace:
            return
        self._pending = None
        renewal = self._renewal and spirit_id == self.spirit.id
        self._renewal = False
        self._handover_active = True
        self._handover_task = asyncio.create_task(self._handover(spirit_id, renewal))

    async def _handover(self, spirit_id: str, renewal: bool) -> None:
        old = self._active
        try:
            if not renewal:
                self._set_state("summoning", force=True)
            spirit = self.registry.get(spirit_id)
            new = await self._open_conn(spirit, self._history())
            self._active = new
            self.spirit = spirit
            if not renewal:
                self._emit_identity(spirit)
        except Exception as e:
            self.emitter.error(f"handover failed: {type(e).__name__}: {e}")
            self._handover_active = False
            self._set_state("listening", force=True)
            self._handover_task = None
            return
        self._handover_active = False
        self._set_state("listening", force=True)
        if old is not None:
            self._spawn_bg(self._close_conn(old))
        self._handover_task = None

    # ------------------------------------------------------------ reader
    async def _read(self, conn: _Conn) -> None:
        try:
            async for ev in conn.stream.receive():
                for name, body in ev.items():
                    await self._on_event(conn, name, body or {})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not self._closing and conn is self._active:
                self.emitter.error(f"stream error: {type(e).__name__}: {e}")
        else:
            if not self._closing and conn is self._active and not conn.closed:
                self.emitter.error("stream ended")
                self._set_state("idle")

    async def _on_event(self, conn: _Conn, name: str, body: dict) -> None:
        if name == "usageEvent":
            self.usage.record(conn.key, body)
            return
        if conn is not self._active:
            return
        if name == "completionStart":
            self._barged = False
        elif name == "contentStart":
            stage = None
            fields = body.get("additionalModelFields")
            if isinstance(fields, str):
                try:
                    stage = json.loads(fields).get("generationStage")
                except ValueError:
                    pass
            if (body.get("role") or "").upper() == "ASSISTANT" and body.get("type") == "TEXT" \
                    and stage == "SPECULATIVE":
                self._spec_blocks += 1
            conn.blocks[body.get("contentId")] = {
                "role": (body.get("role") or "").upper(), "type": body.get("type"),
                "stage": stage, "text": [], "turn": None}
        elif name == "textOutput":
            self._on_text(conn, body)
        elif name == "audioOutput":
            pcm = base64.b64decode(body.get("content", ""))
            self._touch()
            self._cancel_turn_timer()
            self.audio_bytes += len(pcm)
            if pcm and self.state != "speaking":
                self._set_state("speaking")
            for sink in list(self.audio_sinks):
                sink(pcm)
        elif name == "toolUse":
            task = asyncio.create_task(self._run_tool(conn, body))
            conn.pending.add(task)
            task.add_done_callback(conn.pending.discard)
        elif name == "contentEnd":
            self._on_content_end(conn, body)
        elif name == "completionEnd":
            self._close_turn()
        elif name == "serviceError":
            self.emitter.error(body.get("message", "service error"))

    def _on_text(self, conn: _Conn, body: dict) -> None:
        text = body.get("content", "")
        if text.lstrip().startswith("{"):
            try:
                if json.loads(text).get("interrupted") is True:
                    self._barge_in()
                    return
            except (ValueError, AttributeError):
                pass
        block = conn.blocks.setdefault(body.get("contentId"), {
            "role": (body.get("role") or "").upper(), "type": "TEXT",
            "stage": None, "text": [], "turn": None})
        role = block["role"] or (body.get("role") or "").upper()
        final = block["stage"] in (None, "FINAL")
        block["text"].append(text)
        if role == "USER":
            if text.strip():
                self._touch()
            if block["turn"] is None:
                if self._turn_timer:
                    self._close_turn()
                self._user_n += 1
                block["turn"] = f"u{self._user_n}"
                self._barged = False
                self._cold = False
            self.emitter.transcript(block["turn"], "user", " ".join(t.strip() for t in block["text"]), final)
        elif role == "ASSISTANT":
            self._cancel_turn_timer()
            if self._turn is None:
                self._turn_n += 1
                self._turn = {"id": f"s{self._turn_n}", "final": [], "spec": []}
            self._turn["final" if final else "spec"].append(text.strip())
            shown = self._turn["final"] or self._turn["spec"]
            self.emitter.transcript(self._turn["id"], "spirit", " ".join(shown), False)

    def _on_content_end(self, conn: _Conn, body: dict) -> None:
        block = conn.blocks.pop(body.get("contentId"), None) or {}
        stop = body.get("stopReason")
        if stop == "INTERRUPTED" and block.get("type") in (None, "TEXT"):
            self._barge_in()
            return
        if block.get("type") != "TEXT":
            return
        text = " ".join(t.strip() for t in block.get("text", []))
        final = block.get("stage") in (None, "FINAL")
        if block.get("role") == "USER" and final and text:
            self.transcript.append({"role": "user", "text": text})
        elif block.get("role") == "ASSISTANT" and final and text:
            self._final_blocks += 1
            if stop == "END_TURN" or self._final_blocks >= self._spec_blocks > 0:
                self._end_turn_soon()

    def _cancel_turn_timer(self) -> None:
        if self._turn_timer:
            self._turn_timer.cancel()
            self._turn_timer = None

    def _end_turn_soon(self) -> None:
        """Nova's FINAL text blocks arrive at playback pace and a block can end with END_TURN
        while more FINAL blocks follow. Close when every SPECULATIVE block has its FINAL echo;
        otherwise wait turn_grace for the rest (0 closes at once)."""
        self._cancel_turn_timer()
        if self.turn_grace <= 0 or self._final_blocks >= self._spec_blocks:
            self._close_turn()
            return

        async def later():
            await asyncio.sleep(self.turn_grace)
            self._turn_timer = None
            self._close_turn()
        self._turn_timer = asyncio.create_task(later())

    def _close_turn(self, interrupted: bool = False) -> None:
        if self._turn_timer and self._turn_timer is not asyncio.current_task():
            self._turn_timer.cancel()
        self._turn_timer = None
        self._spec_blocks = self._final_blocks = 0
        turn, self._turn = self._turn, None
        if turn:
            finals = " ".join(turn["final"])
            if finals:
                for p in check_turn(finals, self.tool_results, after_cold=self._cold):
                    self.grounding_violations.append(p)
                    self.emitter.error("grounding: " + p)
                self._cold = False
            text = finals if (finals or interrupted) else " ".join(turn["spec"])
            self.emitter.transcript(turn["id"], "spirit", text, True)
            if finals:
                self.transcript.append({"role": "spirit", "text": finals})
        self._set_state("listening")
        self._maybe_start_pending(after_turn=bool(turn))

    def _barge_in(self) -> None:
        if self._barged:
            return
        self._barged = True
        for clear in list(self.clear_sinks):
            clear()
        self._close_turn(interrupted=True)

    # ------------------------------------------------------------ tools
    @staticmethod
    def _model_payload(result: dict) -> str:
        content = result.get("content")
        if isinstance(content, str) and len(content) > MAX_TOOL_CONTENT:
            result = dict(result)
            result["content"] = (content[:MAX_TOOL_CONTENT]
                                 + f"[truncated: {MAX_TOOL_CONTENT} of {len(content)} chars]")
        return json.dumps(result)

    async def _run_tool(self, conn: _Conn, body: dict) -> None:
        name = body.get("toolName", "")
        tool_use_id = body.get("toolUseId", "")
        args: dict = {}
        result: dict
        tool = conn.tools.get(name)
        try:
            raw = body.get("content") or "{}"
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if tool is None:
                raise KeyError(f"unknown tool: {name}")
            result = await self._call_handler(tool, args)
        except Exception as e:
            self.emitter.error(f"tool {name} failed: {type(e).__name__}: {e}")
            result = {"error": f"{type(e).__name__}: {e}"}
            tool = None  # no evidence for failed calls
        if name == SUMMON_TOOL and tool is not None:
            target = args.get("spirit")
            if target in self.registry:
                self._pending = (target, self.clock(), self.summon_grace)
            else:
                result = {"error": f"unknown spirit: {target}"}
        if tool is not None and tool.to_evidence is not None and "error" not in result:
            try:
                self.emitter.evidence(**tool.to_evidence(result))
            except Exception as e:
                self.emitter.error(f"evidence for {name} failed: {type(e).__name__}")
        payload = self._model_payload(result)
        if "error" not in result:
            self.tool_results.append(result)
            if is_cold(result):
                self._cold = True
        self.tool_log.append({"name": name, "args": args, "result": result, "payload": payload})
        try:
            await self._send(conn, *events.tool_result_block(
                conn.prompt, str(uuid.uuid4()), tool_use_id, payload))
        except Exception as e:
            self.emitter.error(f"toolResult for {name} not sent: {type(e).__name__}")

    async def _call_handler(self, tool, args: dict) -> dict:
        work = asyncio.ensure_future(asyncio.to_thread(tool.handler, args))
        done, _ = await asyncio.wait({work}, timeout=self.filler_after)
        if not done:
            try:
                await self.inject_text(FILLER_TEXT)
            except Exception:
                pass
            done, _ = await asyncio.wait({work}, timeout=max(self.tool_timeout - self.filler_after, 0.001))
        if not done:
            work.add_done_callback(lambda f: f.exception())
            raise TimeoutError(f"tool {tool.name} timed out")
        return work.result()
