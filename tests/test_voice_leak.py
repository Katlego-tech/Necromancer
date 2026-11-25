"""The access code must not appear in any output channel."""
import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest

from aiohttp import WSMsgType

from tests.test_voice_access import CODE, Clock, auth, close_code, start
from voice.guard import AccessGate, Limits
from voice.server import GATE, SESSION


class LeakTest(unittest.IsolatedAsyncioTestCase):
    async def test_code_not_in_any_output(self):
        needles = [CODE, base64.b64encode(CODE.encode()).decode(), hashlib.sha256(CODE.encode()).hexdigest()]
        out, err = io.StringIO(), io.StringIO()
        frames = []
        tmp = tempfile.TemporaryDirectory()
        record = os.path.join(tmp.name, "record.jsonl")
        with self.assertLogs(level="DEBUG") as logs, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            logging_probe = __import__("logging").getLogger().debug
            logging_probe("probe")
            client = await start(gate=AccessGate(CODE, delay=0, clock=Clock()),
                                 limits=Limits(disconnect_grace=0.05), record_path=record)
            try:
                async def run(code):
                    ws = await client.ws_connect("/io")
                    frames.append((await ws.receive()).data)
                    await auth(ws, code)
                    try:
                        async with asyncio.timeout(1):
                            while True:
                                msg = await ws.receive()
                                if msg.type == WSMsgType.TEXT:
                                    frames.append(msg.data)
                                elif msg.type != WSMsgType.BINARY:
                                    break
                    except TimeoutError:
                        pass
                    return ws
                for _ in range(5):
                    await run("wrong")
                locked = await run(CODE)       # locked out
                ctl = client.server.app[SESSION]
                await locked.close()
                # a correct attempt on a fresh gate, then session end
                client.server.app[GATE] = AccessGate(CODE, delay=0)
                ws = await run(CODE)
                await asyncio.sleep(0.1)
                await ws.close()
                await ctl.stop("session_limit")
                tmpdir = client._tmp.name
                files = [open(os.path.join(tmpdir, n)).read() for n in os.listdir(tmpdir)]
            finally:
                await client.close()
        files.append(open(record).read())
        blob = "\n".join(frames + files + [out.getvalue(), err.getvalue()] + logs.output)
        for n in needles:
            self.assertNotIn(n, blob)
        client._tmp.cleanup()
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
