"""Bidirectional stream abstraction. The AWS SDK is imported lazily, only by the real stream."""
from __future__ import annotations

import json
from typing import AsyncIterator, Protocol

from voice.events import MODEL_ID, REGION


class BidiStream(Protocol):
    async def send(self, event: dict) -> None: ...
    def receive(self) -> AsyncIterator[dict]: ...
    async def close(self) -> None: ...


class StreamFactory(Protocol):
    async def open(self) -> BidiStream: ...


class BedrockStream:
    """Wraps an SDK bidirectional stream. send() takes inner event dicts."""

    def __init__(self, stream, chunk_cls, part_cls):
        self._stream = stream
        self._chunk_cls = chunk_cls
        self._part_cls = part_cls
        self.last_error: str | None = None

    async def send(self, event: dict) -> None:
        payload = json.dumps({"event": event}).encode("utf-8")
        await self._stream.input_stream.send(
            self._chunk_cls(value=self._part_cls(bytes_=payload)))

    async def receive(self):
        _, receiver = await self._stream.await_output()
        async for result in receiver:
            value = getattr(result, "value", None)
            raw = getattr(value, "bytes_", None)
            if not raw:  # service exception shapes carry a message instead of bytes
                self.last_error = f"{type(result).__name__}: {getattr(value, 'message', value)}"
                yield {"serviceError": {"message": self.last_error}}
                continue
            yield json.loads(raw.decode("utf-8")).get("event", {})

    async def close(self) -> None:
        try:
            await self._stream.input_stream.close()
        except Exception:
            pass


async def open_bedrock_stream(credentials, model: str = MODEL_ID, region: str = REGION) -> BedrockStream:
    from aws_sdk_bedrock_runtime.aio.client import AsyncBedrockRuntimeClient
    from aws_sdk_bedrock_runtime.aio.config import AsyncBedrockRuntimeConfig
    from aws_sdk_bedrock_runtime.models import (
        BidirectionalInputPayloadPart,
        InvokeModelWithBidirectionalStreamInputChunk,
        InvokeModelWithBidirectionalStreamOperationInput,
    )
    from smithy_http.aio.crt import AWSCRTHTTPClient, AWSCRTHTTPClientConfig

    config = await AsyncBedrockRuntimeConfig.resolve(
        region=region,
        endpoint_uri=f"https://bedrock-runtime.{region}.amazonaws.com",
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
        transport=AWSCRTHTTPClient(AWSCRTHTTPClientConfig(force_http_2=True)),
    )
    client = AsyncBedrockRuntimeClient(config=config)
    stream = await client.invoke_model_with_bidirectional_stream(
        InvokeModelWithBidirectionalStreamOperationInput(model_id=model))
    return BedrockStream(stream, InvokeModelWithBidirectionalStreamInputChunk,
                         BidirectionalInputPayloadPart)


class BedrockFactory:
    """Opens real streams. Credentials are loaded fresh for each stream."""

    def __init__(self, load_credentials=None, model: str = MODEL_ID, region: str = REGION):
        if load_credentials is None:
            from voice.credentials import load_credentials
        self._load = load_credentials
        self.model = model
        self.region = region

    async def open(self) -> BedrockStream:
        return await open_bedrock_stream(self._load(), self.model, self.region)
