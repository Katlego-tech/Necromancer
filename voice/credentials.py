"""AWS credentials for the Bedrock stream. Two sources:
  cli   `aws configure export-credentials` (works after `aws login` on a laptop).
  imds  the EC2 instance role, read from IMDSv2 (session-token flow only; IMDSv1 is never used).
NECRO_CREDENTIALS_SOURCE picks one of `auto` (default), `cli` or `imds`. `auto` tries the CLI
first, so the laptop flow is unchanged, and falls back to IMDS when the CLI is missing or fails.
On EC2 set `imds`: it needs no AWS CLI and no child process.
Values are handed to the SDK config only. They are never printed, logged or stored on disk.
Error messages never include response bodies.
"""
from __future__ import annotations
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
class CredentialsError(RuntimeError):
    pass
@dataclass(frozen=True)
class Credentials:
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)
    def __repr__(self) -> str:
        return "Credentials(<redacted>)"
    __str__ = __repr__
def parse_credentials(process_json: str) -> Credentials:
    c = json.loads(process_json)
    return Credentials(c["AccessKeyId"], c["SecretAccessKey"], c.get("SessionToken") or None)
# --------------------------------------------------------------------------- instance role (IMDSv2)
IMDS_DEFAULT_ENDPOINT = "http://169.254.169.254"
IMDS_TOKEN_TTL = 300          # seconds; the metadata token is only needed for the next two calls
IMDS_TIMEOUT = 2.0            # seconds per request
REFRESH_MARGIN = 300.0        # refresh when fewer than this many seconds of validity remain
class ImdsCredentialSource:
    """Temporary credentials of the instance role, cached until shortly before they expire."""
    def __init__(self, endpoint: str | None = None, timeout: float = IMDS_TIMEOUT,
                 clock=time.time, margin: float = REFRESH_MARGIN):
        endpoint = (endpoint or os.environ.get("AWS_EC2_METADATA_SERVICE_ENDPOINT")
                    or IMDS_DEFAULT_ENDPOINT).rstrip("/")
        if not endpoint.startswith("http://"):
            raise CredentialsError("the instance metadata endpoint must be an http:// URL")
        self._endpoint = endpoint
        self._timeout = timeout
        self._clock = clock
        self._margin = margin
        self._cached: Credentials | None = None
        self._expires = 0.0
        self._lock = threading.Lock()
    def _call(self, method: str, path: str, headers: dict) -> str:
        req = urllib.request.Request(self._endpoint + path, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.read(65536).decode("utf-8")
        except urllib.error.HTTPError as e:
            raise CredentialsError(f"instance metadata returned HTTP {e.code} for {path}") from None
        except (urllib.error.URLError, OSError, ValueError):
            raise CredentialsError("instance metadata service is not reachable") from None
    def _fetch(self) -> tuple[Credentials, float]:
        token = self._call("PUT", "/latest/api/token",
                           {"X-aws-ec2-metadata-token-ttl-seconds": str(IMDS_TOKEN_TTL)})
        auth = {"X-aws-ec2-metadata-token": token}
        base = "/latest/meta-data/iam/security-credentials/"
        try:
            roles = self._call("GET", base, auth).split()
        except CredentialsError as e:
            if "HTTP 404" in str(e):  # IMDS answers 404 when no instance profile is attached
                raise CredentialsError("no IAM role is attached to this instance") from None
            raise
        if not roles:
            raise CredentialsError("no IAM role is attached to this instance")
        body = self._call("GET", base + roles[0], auth)
        try:
            c = json.loads(body)
            if c.get("Code", "Success") != "Success":
                raise CredentialsError("instance metadata reported no usable credentials")
            expires = datetime.strptime(c["Expiration"], "%Y-%m-%dT%H:%M:%SZ") \
                .replace(tzinfo=timezone.utc).timestamp()
            return Credentials(c["AccessKeyId"], c["SecretAccessKey"], c["Token"]), expires
        except (ValueError, KeyError, TypeError, AttributeError):
            raise CredentialsError("unexpected instance metadata credentials format") from None
    def get(self) -> Credentials:
        with self._lock:
            if self._cached is None or self._clock() >= self._expires - self._margin:
                self._cached, self._expires = self._fetch()
            return self._cached
_imds: ImdsCredentialSource | None = None
def _shared_imds() -> ImdsCredentialSource:
    global _imds
    if _imds is None:
        _imds = ImdsCredentialSource()
    return _imds
def load_imds_credentials() -> Credentials:
    return _shared_imds().get()
# --------------------------------------------------------------------------- local CLI
def load_cli_credentials(timeout: float = 30) -> Credentials:
    try:
        out = subprocess.run(["aws", "configure", "export-credentials", "--format", "process"],
                             capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raise CredentialsError("could not export AWS credentials (run `aws login`)") from None
    if out.returncode != 0:
        raise CredentialsError("could not export AWS credentials (run `aws login`)")
    try:
        return parse_credentials(out.stdout)
    except (ValueError, KeyError):
        raise CredentialsError("unexpected output from `aws configure export-credentials`") from None
def load_credentials(timeout: float = 30) -> Credentials:
    source = os.environ.get("NECRO_CREDENTIALS_SOURCE", "auto").strip().lower() or "auto"
    if source == "cli":
        return load_cli_credentials(timeout)
    if source == "imds":
        return load_imds_credentials()
    if source != "auto":
        raise CredentialsError("NECRO_CREDENTIALS_SOURCE must be auto, cli or imds")
    try:
        return load_cli_credentials(timeout)
    except CredentialsError as cli_error:
        try:
            return load_imds_credentials()
        except CredentialsError:
            raise CredentialsError(f"{cli_error}; the EC2 instance role is not available either") from None
