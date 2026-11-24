"""One-shot smoke test for Amazon Nova Sonic bidirectional streaming.

Sends a short system prompt and one USER text turn, then prints the event types
that come back, any text, and how many bytes of audio arrived. No microphone needed.

Run with the project venv (Python 3.12+):
    .venv/bin/python scripts/nova_smoke_test.py [--model amazon.nova-2-5-sonic] [--region us-east-1]

Credentials come from `aws configure export-credentials` (works after `aws login`) and are
placed in this process's environment only. They are never printed or written to disk.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import uuid

from aws_sdk_bedrock_runtime.aio.client import AsyncBedrockRuntimeClient
from aws_sdk_bedrock_runtime.aio.config import AsyncBedrockRuntimeConfig
from aws_sdk_bedrock_runtime.models import (
    BidirectionalInputPayloadPart,
    InvokeModelWithBidirectionalStreamInputChunk,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from smithy_http.aio.crt import AWSCRTHTTPClient, AWSCRTHTTPClientConfig


def load_credentials_into_env() -> None:
    out = subprocess.run(
        ["aws", "configure", "export-credentials", "--format", "process"],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        sys.exit("could not export AWS credentials (run `aws login`): " + out.stderr.strip()[:200])
    c = json.loads(out.stdout)
    os.environ["AWS_ACCESS_KEY_ID"] = c["AccessKeyId"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = c["SecretAccessKey"]
    if c.get("SessionToken"):
        os.environ["AWS_SESSION_TOKEN"] = c["SessionToken"]


async def run(model: str, region: str, seconds: float) -> int:
    load_credentials_into_env()
    config = await AsyncBedrockRuntimeConfig.resolve(
        region=region,
        endpoint_uri=f"https://bedrock-runtime.{region}.amazonaws.com",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
        transport=AWSCRTHTTPClient(AWSCRTHTTPClientConfig(force_http_2=True)),
    )
    client = AsyncBedrockRuntimeClient(config=config)
    stream = await client.invoke_model_with_bidirectional_stream(
        InvokeModelWithBidirectionalStreamOperationInput(model_id=model))

    async def send(event: dict) -> None:
        payload = json.dumps({"event": event}).encode("utf-8")
        await stream.input_stream.send(InvokeModelWithBidirectionalStreamInputChunk(
            value=BidirectionalInputPayloadPart(bytes_=payload)))

    prompt, sys_c, user_c = (str(uuid.uuid4()) for _ in range(3))

    async def text_block(content_name: str, role: str, text: str, interactive: bool) -> None:
        await send({"contentStart": {"promptName": prompt, "contentName": content_name,
                                     "type": "TEXT", "interactive": interactive, "role": role,
                                     "textInputConfiguration": {"mediaType": "text/plain"}}})
        await send({"textInput": {"promptName": prompt, "contentName": content_name,
                                  "content": text}})
        await send({"contentEnd": {"promptName": prompt, "contentName": content_name}})

    await send({"sessionStart": {"inferenceConfiguration": {
        "maxTokens": 256, "topP": 0.9, "temperature": 0.7}}})
    await send({"promptStart": {
        "promptName": prompt,
        "textOutputConfiguration": {"mediaType": "text/plain"},
        "audioOutputConfiguration": {"mediaType": "audio/lpcm", "sampleRateHertz": 24000,
                                     "sampleSizeBits": 16, "channelCount": 1,
                                     "voiceId": "matthew", "encoding": "base64",
                                     "audioType": "SPEECH"}}})
    await text_block(sys_c, "SYSTEM", "You are a test assistant. Answer in one short sentence.", False)
    # Cross-modal text needs a continuously streaming session, so keep an audio block
    # open and feed it silence (16 kHz, 16-bit mono). No microphone involved.
    audio_c = str(uuid.uuid4())
    await send({"contentStart": {"promptName": prompt, "contentName": audio_c,
                                 "type": "AUDIO", "interactive": True, "role": "USER",
                                 "audioInputConfiguration": {
                                     "mediaType": "audio/lpcm", "sampleRateHertz": 16000,
                                     "sampleSizeBits": 16, "channelCount": 1,
                                     "audioType": "SPEECH", "encoding": "base64"}}})
    silence = base64.b64encode(bytes(1024 * 2)).decode()  # 1024 samples, ~64 ms

    async def feed_silence() -> None:
        while True:
            await send({"audioInput": {"promptName": prompt, "contentName": audio_c,
                                       "content": silence}})
            await asyncio.sleep(0.064)

    silence_task = asyncio.create_task(feed_silence())
    await asyncio.sleep(0.5)
    await text_block(user_c, "USER", "Say hello and tell me you are working.", True)

    seen: dict[str, int] = {}
    texts: list[str] = []
    audio_bytes = 0
    error = None

    async def reader() -> None:
        nonlocal audio_bytes, error
        _, receiver = await stream.await_output()
        async for result in receiver:
            value = getattr(result, "value", None)
            raw = getattr(value, "bytes_", None)
            if not raw:  # service exception shapes carry a message instead of bytes
                error = f"{type(result).__name__}: {getattr(value, 'message', value)}"
                continue
            ev = json.loads(raw.decode("utf-8")).get("event", {})
            if os.environ.get("NOVA_DEBUG"):
                print("EVENT", json.dumps(ev)[:300], file=sys.stderr)
            for name, body in ev.items():
                seen[name] = seen.get(name, 0) + 1
                if name == "textOutput":
                    texts.append(body.get("content", ""))
                elif name == "audioOutput":
                    audio_bytes += len(base64.b64decode(body.get("content", "")))

    task = asyncio.create_task(reader())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=seconds)
    except asyncio.TimeoutError:
        pass
    except Exception as e:  # report whatever the service said
        error = f"{type(e).__name__}: {e}"
    if task.done() and not task.cancelled() and task.exception() and error is None:
        error = f"{type(task.exception()).__name__}: {task.exception()}"
    task.cancel()
    silence_task.cancel()
    try:
        await send({"contentEnd": {"promptName": prompt, "contentName": audio_c}})
        await send({"promptEnd": {"promptName": prompt}})
        await send({"sessionEnd": {}})
        await stream.input_stream.close()
    except Exception:
        pass

    print(f"model={model} region={region}")
    print("event types received:", seen or "none")
    print("text output:", texts or "none")
    print("audio bytes received:", audio_bytes)
    if error:
        print("error:", error[:500])
    return 0 if (audio_bytes > 0 and not error) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="amazon.nova-2-5-sonic")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--seconds", type=float, default=20.0)
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.model, a.region, a.seconds)))
