"""Pure builders for Nova Sonic bidirectional input events.

Each builder returns the inner event dict, for example {"sessionStart": {...}}.
The stream layer wraps it as {"event": ...} on the wire.
"""
from __future__ import annotations

import base64
import json

MODEL_ID = "amazon.nova-2-5-sonic"
REGION = "us-east-1"
INPUT_SAMPLE_RATE = 16000
OUTPUT_SAMPLE_RATE = 24000


def session_start(max_tokens: int = 1024, top_p: float = 0.9, temperature: float = 0.7) -> dict:
    return {"sessionStart": {"inferenceConfiguration": {
        "maxTokens": max_tokens, "topP": top_p, "temperature": temperature}}}


def tool_configuration(tools: list[dict]) -> dict:
    """tools: [{name, description, input_schema}] -> promptStart toolConfiguration."""
    return {"tools": [{"toolSpec": {
        "name": t["name"], "description": t["description"],
        "inputSchema": {"json": json.dumps(t["input_schema"])}}} for t in tools],
        "toolChoice": {"auto": {}}}


def prompt_start(prompt_name: str, voice_id: str, tools: list[dict] | None = None) -> dict:
    body = {
        "promptName": prompt_name,
        "textOutputConfiguration": {"mediaType": "text/plain"},
        "audioOutputConfiguration": {
            "mediaType": "audio/lpcm", "sampleRateHertz": OUTPUT_SAMPLE_RATE,
            "sampleSizeBits": 16, "channelCount": 1, "voiceId": voice_id,
            "encoding": "base64", "audioType": "SPEECH"},
    }
    if tools:
        body["toolUseOutputConfiguration"] = {"mediaType": "application/json"}
        body["toolConfiguration"] = tool_configuration(tools)
    return {"promptStart": body}


def text_block(prompt_name: str, content_name: str, role: str, text: str,
               interactive: bool) -> list[dict]:
    """contentStart, textInput, contentEnd for a TEXT block (SYSTEM, USER or ASSISTANT)."""
    return [
        {"contentStart": {"promptName": prompt_name, "contentName": content_name,
                          "type": "TEXT", "interactive": interactive, "role": role,
                          "textInputConfiguration": {"mediaType": "text/plain"}}},
        {"textInput": {"promptName": prompt_name, "contentName": content_name, "content": text}},
        {"contentEnd": {"promptName": prompt_name, "contentName": content_name}},
    ]


def audio_start(prompt_name: str, content_name: str) -> dict:
    return {"contentStart": {
        "promptName": prompt_name, "contentName": content_name, "type": "AUDIO",
        "interactive": True, "role": "USER",
        "audioInputConfiguration": {
            "mediaType": "audio/lpcm", "sampleRateHertz": INPUT_SAMPLE_RATE,
            "sampleSizeBits": 16, "channelCount": 1, "audioType": "SPEECH",
            "encoding": "base64"}}}


def audio_input(prompt_name: str, content_name: str, pcm: bytes) -> dict:
    return {"audioInput": {"promptName": prompt_name, "contentName": content_name,
                           "content": base64.b64encode(pcm).decode("ascii")}}


def audio_end(prompt_name: str, content_name: str) -> dict:
    return {"contentEnd": {"promptName": prompt_name, "contentName": content_name}}


def tool_result_block(prompt_name: str, content_name: str, tool_use_id: str,
                      payload: str) -> list[dict]:
    """contentStart (TOOL), toolResult, contentEnd. payload is the JSON string for the model."""
    return [
        {"contentStart": {"promptName": prompt_name, "contentName": content_name,
                          "interactive": False, "type": "TOOL", "role": "TOOL",
                          "toolResultInputConfiguration": {
                              "toolUseId": tool_use_id, "type": "TEXT",
                              "textInputConfiguration": {"mediaType": "text/plain"}}}},
        {"toolResult": {"promptName": prompt_name, "contentName": content_name,
                        "content": payload}},
        {"contentEnd": {"promptName": prompt_name, "contentName": content_name}},
    ]


def history_blocks(prompt_name: str, turns: list[dict], new_name) -> list[dict]:
    """turns: [{"role": "USER"|"ASSISTANT", "text": str}] -> text blocks, interactive false."""
    out: list[dict] = []
    for t in turns:
        out.extend(text_block(prompt_name, new_name(), t["role"], t["text"], False))
    return out


def prompt_end(prompt_name: str) -> dict:
    return {"promptEnd": {"promptName": prompt_name}}


def session_end() -> dict:
    return {"sessionEnd": {}}
