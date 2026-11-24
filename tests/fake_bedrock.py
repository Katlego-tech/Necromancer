"""Fake Bedrock stream for offline voice tests. Records every event the engine sends."""
import asyncio
import base64
import json
import os
import uuid

from voice.summon import Spirit, ToolDef

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COSTUMES = os.path.join(ROOT, "mascot", "costumes")
COLD = "The trail goes cold."

EMAIL_RESULT = {
    "source": "ev-0001",
    "content": "Hello Bob,\nthe quarterly numbers need another review before Friday.\nAlice",
    "summary": "1 email found.",
    "evidence": [{"evidence_id": "ev-0001", "sender": "alice@example.invalid",
                  "recipients": ["bob@example.invalid"], "date": "2001-01-01T00:00:00Z",
                  "subject": "Numbers", "source_path": "x/y/1.",
                  "excerpt": "another review before Friday"}],
}
COLD_RESULT = {"source": None, "content": None, "summary": COLD, "evidence": []}


def email_evidence(result):
    if result["source"] is None:
        return {"kind": "email", "source": None, "content": None}
    meta = {k: v for k, v in result["evidence"][0].items() if k != "excerpt"}
    return {"kind": "email", "source": result["source"], "content": result["content"],
            "metadata": meta, "excerpt": result["evidence"][0]["excerpt"]}


def lookup_tool(handler):
    return ToolDef("lookup", "Look up mail.", {"type": "object", "properties": {
        "query": {"type": "string"}}}, handler, email_evidence)


def make_spirit(spirit_id="enron", voice="matthew", handler=None, appearance=True, persona=None):
    tools = (lookup_tool(handler or (lambda args: dict(EMAIL_RESULT))),)
    return Spirit(spirit_id, persona or f"You are the {spirit_id} test spirit.", voice, tools,
                  os.path.join(COSTUMES, f"{spirit_id}.json") if appearance else None)


# ---- scripted output events -------------------------------------------------
def completion_start():
    return [{"completionStart": {"completionId": str(uuid.uuid4())}}]


def completion_end():
    return [{"completionEnd": {}}]


def user_asr(text, stage="FINAL"):
    cid = str(uuid.uuid4())
    return [{"contentStart": {"contentId": cid, "type": "TEXT", "role": "USER",
                              "additionalModelFields": json.dumps({"generationStage": stage})}},
            {"textOutput": {"contentId": cid, "role": "USER", "content": text}},
            {"contentEnd": {"contentId": cid, "type": "TEXT", "stopReason": "PARTIAL_TURN"}}]


def assistant_text(text, stage="FINAL", stop="PARTIAL_TURN"):
    cid = str(uuid.uuid4())
    return [{"contentStart": {"contentId": cid, "type": "TEXT", "role": "ASSISTANT",
                              "additionalModelFields": json.dumps({"generationStage": stage})}},
            {"textOutput": {"contentId": cid, "role": "ASSISTANT", "content": text}},
            {"contentEnd": {"contentId": cid, "type": "TEXT", "stopReason": stop}}]


def audio_out(pcm=b"\x01\x02" * 8):
    return [{"audioOutput": {"content": base64.b64encode(pcm).decode()}}]


def tool_use(name, tool_use_id, args):
    return [{"toolUse": {"toolName": name, "toolUseId": tool_use_id, "role": "TOOL",
                         "content": json.dumps(args)}}]


def interrupted():
    cid = str(uuid.uuid4())
    return [{"contentStart": {"contentId": cid, "type": "TEXT", "role": "ASSISTANT"}},
            {"textOutput": {"contentId": cid, "role": "ASSISTANT",
                            "content": '{ "interrupted" : true }'}},
            {"contentEnd": {"contentId": cid, "type": "TEXT", "stopReason": "INTERRUPTED"}}]


class Step:
    def __init__(self, outputs, text=None, audio_n=None):
        self.outputs, self.text, self.audio_n, self.fired = outputs, text, audio_n, False


def on_text(substr, *outputs):
    return Step(list(outputs), text=substr)


def on_audio(n, *outputs):
    return Step(list(outputs), audio_n=n)


class FakeStream:
    def __init__(self, script=()):
        self.sent = []
        self.closed = False
        self.close_index = None
        self.script = list(script)
        self._q = asyncio.Queue()
        self._blocks = {}
        self._audio_n = 0

    def names(self):
        return [next(iter(e)) for e in self.sent]

    def of(self, name):
        return [e[name] for e in self.sent if name in e]

    def audio_payloads(self):
        return [base64.b64decode(a["content"]) for a in self.of("audioInput")]

    def tool_results(self):
        return self.of("toolResult")

    def push(self, *groups):
        for g in groups:
            for ev in g:
                self._q.put_nowait(ev)

    def _fire(self, step):
        step.fired = True
        self.push(*step.outputs)

    async def send(self, event):
        if self.closed:
            raise RuntimeError("send on closed fake stream")
        self.sent.append(event)
        name = next(iter(event))
        body = event[name]
        if name == "contentStart":
            self._blocks[body["contentName"]] = {"role": body.get("role"), "type": body.get("type"),
                                                  "interactive": body.get("interactive"), "text": ""}
        elif name == "textInput":
            self._blocks[body["contentName"]]["text"] = body["content"]
        elif name == "contentEnd":
            blk = self._blocks.get(body["contentName"])
            if blk and blk["type"] == "TEXT" and blk["role"] == "USER" and blk["interactive"]:
                for st in self.script:
                    if not st.fired and st.text is not None and st.text in blk["text"]:
                        self._fire(st)
                        break
        elif name == "audioInput":
            self._audio_n += 1
            for st in self.script:
                if not st.fired and st.audio_n == self._audio_n:
                    self._fire(st)

    async def receive(self):
        while True:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def close(self):
        self.closed = True
        self.close_index = len(self.sent)
        self._q.put_nowait(None)


class FakeFactory:
    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.streams = []

    async def open(self):
        script = self.scripts[len(self.streams)] if len(self.streams) < len(self.scripts) else ()
        s = FakeStream(script)
        self.streams.append(s)
        return s


async def wait_for(cond, timeout=3.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)
