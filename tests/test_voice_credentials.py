"""Credential sources: the local CLI flow and the EC2 instance role via a fake IMDSv2 endpoint. Offline."""
import json
import os
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from voice import credentials as creds
from voice.credentials import CredentialsError, ImdsCredentialSource

SECRET = "SECRETKEY-do-not-leak"
TOKEN = "meta-token-123"


def _body(expiration="2099-01-01T00:00:00Z", key="AKIAFAKE1", code="Success"):
    return json.dumps({"Code": code, "AccessKeyId": key, "SecretAccessKey": SECRET,
                       "Token": "SESSIONTOKEN", "Expiration": expiration})


class FakeImds:
    """Minimal IMDSv2: PUT token, then token-protected GETs. `mode` injects failures."""
    def __init__(self, role="necro-role", creds_body=None, imdsv1_allowed=False):
        self.role = role
        self.creds_body = creds_body or _body()
        self.imdsv1_allowed = imdsv1_allowed
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, text=""):
                data = text.encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_PUT(self):
                outer.requests.append(("PUT", self.path, {k.lower(): v for k, v in self.headers.items()}))
                if self.path == "/latest/api/token" and self.headers.get("X-aws-ec2-metadata-token-ttl-seconds"):
                    self._send(200, TOKEN)
                else:
                    self._send(400)

            def do_GET(self):
                outer.requests.append(("GET", self.path, {k.lower(): v for k, v in self.headers.items()}))
                if self.headers.get("X-aws-ec2-metadata-token") != TOKEN and not outer.imdsv1_allowed:
                    return self._send(401)
                base = "/latest/meta-data/iam/security-credentials/"
                if self.path == base:
                    self._send(200 if outer.role else 404, outer.role or "")
                elif outer.role and self.path == base + outer.role:
                    self._send(200, outer.creds_body)
                else:
                    self._send(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=lambda: self.server.serve_forever(0.02), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def cred_fetches(self):
        return [r for r in self.requests if r[0] == "GET" and r[1].endswith("necro-role")]


class ImdsTests(unittest.TestCase):
    def setUp(self):
        self.imds = FakeImds()
        self.addCleanup(self.imds.close)

    def test_reads_role_credentials_with_imdsv2_token(self):
        c = ImdsCredentialSource(self.imds.url).get()
        self.assertEqual((c.access_key_id, c.secret_access_key, c.session_token),
                         ("AKIAFAKE1", SECRET, "SESSIONTOKEN"))
        methods = [(m, p) for m, p, _ in self.imds.requests]
        self.assertEqual(methods[0], ("PUT", "/latest/api/token"))
        for m, p, headers in self.imds.requests[1:]:
            self.assertEqual(headers.get("x-aws-ec2-metadata-token"), TOKEN)

    def test_cached_until_near_expiry_then_refreshed(self):
        now = [1_700_000_000.0]
        # Expiration = 2023-11-14T22:13:20Z + 1 hour
        self.imds.creds_body = _body("2023-11-14T23:13:20Z")
        src = ImdsCredentialSource(self.imds.url, clock=lambda: now[0], margin=300)
        src.get()
        src.get()
        self.assertEqual(len(self.imds.cred_fetches()), 1)
        now[0] += 3000          # still 600 s of validity
        src.get()
        self.assertEqual(len(self.imds.cred_fetches()), 1)
        now[0] += 400           # 200 s left, inside the 300 s margin
        self.imds.creds_body = _body("2023-11-15T05:13:20Z", key="AKIAFAKE2")
        self.assertEqual(src.get().access_key_id, "AKIAFAKE2")
        self.assertEqual(len(self.imds.cred_fetches()), 2)

    def test_no_role_attached(self):
        self.imds.role = ""
        with self.assertRaisesRegex(CredentialsError, "no IAM role"):
            ImdsCredentialSource(self.imds.url).get()

    def test_bad_code_and_bad_json_are_errors_without_leaking_secrets(self):
        for body in (_body(code="AssumeRoleUnauthorizedAccess"), "not json", json.dumps({"Code": "Success"}),
                     _body(expiration="tomorrow")):
            self.imds.creds_body = body
            with self.assertRaises(CredentialsError) as cm:
                ImdsCredentialSource(self.imds.url).get()
            self.assertNotIn(SECRET, str(cm.exception))
            self.assertNotIn("SESSIONTOKEN", str(cm.exception))

    def test_unreachable_endpoint(self):
        self.imds.close()
        with self.assertRaisesRegex(CredentialsError, "not reachable"):
            ImdsCredentialSource(self.imds.url, timeout=0.5).get()

    def test_https_or_odd_endpoint_refused(self):
        with self.assertRaises(CredentialsError):
            ImdsCredentialSource("file:///etc/passwd")
        with self.assertRaises(CredentialsError):
            ImdsCredentialSource("https://example.com")

    def test_endpoint_from_aws_env_var(self):
        with mock.patch.dict(os.environ, {"AWS_EC2_METADATA_SERVICE_ENDPOINT": self.imds.url}):
            self.assertEqual(ImdsCredentialSource().get().access_key_id, "AKIAFAKE1")

    def test_credentials_repr_is_redacted(self):
        c = ImdsCredentialSource(self.imds.url).get()
        self.assertNotIn(SECRET, repr(c) + str(c))


class LoadCredentialsTests(unittest.TestCase):
    CLI_OUT = json.dumps({"Version": 1, "AccessKeyId": "AKIACLI", "SecretAccessKey": "cli-secret",
                          "SessionToken": "cli-token"})

    def setUp(self):
        self.imds = FakeImds()
        self.addCleanup(self.imds.close)
        patcher = mock.patch.dict(os.environ, {"AWS_EC2_METADATA_SERVICE_ENDPOINT": self.imds.url})
        patcher.start()
        self.addCleanup(patcher.stop)
        creds._imds = None
        self.addCleanup(setattr, creds, "_imds", None)

    def _env(self, source):
        p = mock.patch.dict(os.environ, {"NECRO_CREDENTIALS_SOURCE": source})
        p.start()
        self.addCleanup(p.stop)

    def _cli(self, returncode=0, stdout=None, error=None):
        def run(*a, **k):
            if error:
                raise error
            return subprocess.CompletedProcess(a, returncode, stdout if stdout is not None else self.CLI_OUT, "")
        return mock.patch("voice.credentials.subprocess.run", side_effect=run)

    def test_default_uses_cli_and_never_touches_metadata(self):
        os.environ.pop("NECRO_CREDENTIALS_SOURCE", None)
        with self._cli() as run:
            c = creds.load_credentials()
        self.assertEqual(c.access_key_id, "AKIACLI")
        self.assertEqual(run.call_args[0][0], ["aws", "configure", "export-credentials", "--format", "process"])
        self.assertEqual(self.imds.requests, [])

    def test_auto_falls_back_to_instance_role_when_cli_missing_or_failing(self):
        os.environ.pop("NECRO_CREDENTIALS_SOURCE", None)
        for kwargs in ({"error": FileNotFoundError()}, {"returncode": 255}):
            creds._imds = None
            with self._cli(**kwargs):
                self.assertEqual(creds.load_credentials().access_key_id, "AKIAFAKE1")

    def test_imds_mode_never_runs_the_cli(self):
        self._env("imds")
        with self._cli() as run:
            self.assertEqual(creds.load_credentials().access_key_id, "AKIAFAKE1")
        run.assert_not_called()

    def test_cli_mode_never_reads_metadata(self):
        self._env("cli")
        with self._cli(returncode=1):
            with self.assertRaisesRegex(CredentialsError, "aws login"):
                creds.load_credentials()
        self.assertEqual(self.imds.requests, [])

    def test_auto_error_when_both_fail_mentions_both_and_no_secret(self):
        os.environ.pop("NECRO_CREDENTIALS_SOURCE", None)
        self.imds.role = ""
        with self._cli(returncode=1):
            with self.assertRaises(CredentialsError) as cm:
                creds.load_credentials()
        self.assertIn("aws login", str(cm.exception))
        self.assertIn("instance role", str(cm.exception))

    def test_unknown_source_rejected(self):
        self._env("nonsense")
        with self.assertRaises(CredentialsError):
            creds.load_credentials()


if __name__ == "__main__":
    unittest.main()
