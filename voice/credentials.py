"""AWS credentials from `aws configure export-credentials` (works after `aws login`).

Values are handed to the SDK config only. They are never printed, logged or stored.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field


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


def load_credentials(timeout: float = 30) -> Credentials:
    out = subprocess.run(["aws", "configure", "export-credentials", "--format", "process"],
                         capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise CredentialsError("could not export AWS credentials (run `aws login`)")
    try:
        return parse_credentials(out.stdout)
    except (ValueError, KeyError):
        raise CredentialsError("unexpected output from `aws configure export-credentials`") from None
