"""Event builders, protocol checker, emitter, credentials, registry and import hygiene."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

from tests.fake_bedrock import ROOT
from voice import events, grounding_check
from voice.credentials import Credentials, parse_credentials
from voice.emitter import Emitter
from voice.protocol_check import validate_event
from voice.summon import Registry, Spirit, summon_tool_def


class BuilderTest(unittest.TestCase):
    def test_prompt_start_has_tools_and_voice(self):
        tools = [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
        ev = events.prompt_start("p", "matthew", tools)["promptStart"]
        self.assertEqual(ev["audioOutputConfiguration"]["voiceId"], "matthew")
        spec = ev["toolConfiguration"]["tools"][0]["toolSpec"]
        self.assertEqual(json.loads(spec["inputSchema"]["json"]), {"type": "object"})
        self.assertEqual(ev["toolConfiguration"]["toolChoice"], {"auto": {}})
        self.assertIn("toolUseOutputConfiguration", ev)

    def test_prompt_start_without_tools_has_no_tool_config(self):
        self.assertNotIn("toolConfiguration", events.prompt_start("p", "matthew")["promptStart"])

    def test_tool_result_block_shape(self):
        a, b, c = events.tool_result_block("p", "c", "tid", '{"x":1}')
        self.assertEqual(a["contentStart"]["type"], "TOOL")
        self.assertEqual(a["contentStart"]["role"], "TOOL")
        self.assertFalse(a["contentStart"]["interactive"])
        self.assertEqual(a["contentStart"]["toolResultInputConfiguration"]["toolUseId"], "tid")
        self.assertEqual(b["toolResult"]["content"], '{"x":1}')
        self.assertIn("contentEnd", c)

    def test_text_block_and_history(self):
        blocks = events.text_block("p", "c", "SYSTEM", "hi", False)
        self.assertEqual([next(iter(b)) for b in blocks], ["contentStart", "textInput", "contentEnd"])
        names = iter("abcdef")
        h = events.history_blocks("p", [{"role": "USER", "text": "q"}, {"role": "ASSISTANT", "text": "a"}],
                                  lambda: next(names))
        self.assertEqual(len(h), 6)
        self.assertEqual(h[0]["contentStart"]["role"], "USER")
        self.assertEqual(h[3]["contentStart"]["role"], "ASSISTANT")


class ProtocolCheckTest(unittest.TestCase):
    def _blocks(self, fence):
        text = open(os.path.join(ROOT, "ui", "EVENTS.md"), encoding="utf-8").read()
        return [json.loads(m) for m in re.findall(r"```%s\n(.*?)\n```" % fence, text, re.S)]

    def test_documented_examples_are_valid(self):
        examples = self._blocks("json")
        self.assertGreater(len(examples), 5)
        for ex in examples:
            self.assertIsNone(validate_event(ex), ex)

    def test_documented_invalid_examples_are_rejected(self):
        bad = self._blocks("json-invalid")
        self.assertGreater(len(bad), 3)
        for ex in bad:
            self.assertIsNotNone(validate_event(ex), ex)

    def test_cold_evidence_valid_and_invented_content_invalid(self):
        self.assertIsNone(validate_event({"type": "evidence", "kind": "email", "source": None, "content": None}))
        self.assertIsNotNone(validate_event({"type": "evidence", "kind": "email", "source": None, "content": "x"}))


class EmitterTest(unittest.TestCase):
    def test_invalid_event_is_replaced_by_error(self):
        em = Emitter()
        got = []
        em.add_sink(got.append)
        self.assertFalse(em.emit({"type": "state", "state": "dancing"}))
        self.assertEqual(got[0]["type"], "error")
        self.assertEqual(len(em.violations), 1)

    def test_recorder_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "e.jsonl")
            em = Emitter(path)
            em.state("idle")
            em.transcript("u1", "user", "hi", True)
            em.close()
            lines = [json.loads(x) for x in open(path, encoding="utf-8")]
        self.assertEqual([x["type"] for x in lines], ["state", "transcript"])


class CredentialsTest(unittest.TestCase):
    def test_repr_never_shows_values(self):
        c = parse_credentials(json.dumps({"AccessKeyId": "AKIAEXAMPLE", "SecretAccessKey": "sekrit",
                                          "SessionToken": "tok"}))
        for text in (repr(c), str(c), f"{c}"):
            for secret in ("AKIAEXAMPLE", "sekrit", "tok"):
                self.assertNotIn(secret, text)
        self.assertEqual(c.access_key_id, "AKIAEXAMPLE")


class RegistryTest(unittest.TestCase):
    def test_register_get_ids_and_summon_tool(self):
        r = Registry()
        r.register(Spirit("enron", "p", "matthew"))
        r.register(Spirit("wayback", "p", "tiffany"))
        self.assertEqual(r.ids(), ["enron", "wayback"])
        self.assertEqual(r.get("wayback").voice_id, "tiffany")
        self.assertEqual(summon_tool_def(r).input_schema["properties"]["spirit"]["enum"],
                         ["enron", "wayback"])
        with self.assertRaises(KeyError):
            r.get("nope")


class GroundingCheckTest(unittest.TestCase):
    def test_quote_must_be_in_tool_content(self):
        results = [{"content": "the quarterly numbers   need\nanother review"}]
        self.assertEqual(grounding_check.check_turn(
            'He wrote "the quarterly numbers need another review".', results), [])
        self.assertEqual(len(grounding_check.check_turn(
            'He wrote "the quarterly numbers were fine and approved".', results)), 1)

    def test_short_quotes_ignored_and_cold_must_be_exact(self):
        self.assertEqual(grounding_check.check_turn('He said "no way".', []), [])
        self.assertEqual(grounding_check.check_turn(grounding_check.COLD, [], after_cold=True), [])
        self.assertEqual(len(grounding_check.check_turn("Probably nothing.", [], after_cold=True)), 1)


class ImportHygieneTest(unittest.TestCase):
    def test_session_does_not_import_aws_sdk_or_spirits(self):
        code = ("import sys, voice.session, voice.stream, voice.emitter; "
                "bad=[m for m in sys.modules if m.startswith(('aws_sdk','smithy','awscrt','spirits'))]; "
                "print(bad); sys.exit(1 if bad else 0)")
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_voice_source_never_mentions_spirits_package(self):
        vdir = os.path.join(ROOT, "voice")
        for fn in os.listdir(vdir):
            if fn.endswith(".py"):
                src = open(os.path.join(vdir, fn), encoding="utf-8").read()
                self.assertNotRegex(src, r"(from|import)\s+spirits", fn)


if __name__ == "__main__":
    unittest.main()
