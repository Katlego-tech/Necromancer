"""Live Bedrock test. Skipped unless NECRO_LIVE=1; the default suite never touches AWS."""
import asyncio
import os
import unittest

from voice.emitter import Emitter
from voice.session import VoiceSession
from voice.stream import BedrockFactory
from voice.summon import Registry, Spirit


@unittest.skipUnless(os.environ.get("NECRO_LIVE") == "1", "set NECRO_LIVE=1 to call real Bedrock")
class LiveBedrockTest(unittest.IsolatedAsyncioTestCase):
    async def test_text_question_gets_text_and_audio(self):
        reg = Registry()
        reg.register(Spirit("enron", "You are a helpful assistant. Answer in one short sentence.",
                            "matthew", (), None))
        emitter = Emitter()
        session = VoiceSession(reg, "enron", BedrockFactory(), emitter)
        await session.start()
        try:
            await session.send_text("Say hello.")
            async with asyncio.timeout(60):
                while not (session.audio_bytes > 0 and any(
                        e["type"] == "transcript" and e["role"] == "spirit" and e["final"]
                        for e in emitter.events)):
                    await asyncio.sleep(0.25)
        finally:
            await session.close()
        self.assertGreater(session.audio_bytes, 0)
