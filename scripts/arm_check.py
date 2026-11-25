"""Check from PyPI metadata that every installed dependency has a Python 3.12 aarch64 wheel.

READ-ONLY network use (https://pypi.org/pypi/<name>/<version>/json). The set checked is every
distribution installed in the current environment, which for this project is exactly
requirements.txt plus its dependency closure.

  .venv/bin/python scripts/arm_check.py [--platform aarch64] [--py 312]

Verdict per package: PURE (only a py3-none-any wheel), WHEEL (a cp312 or abi3 manylinux/musllinux aarch64
wheel; a pure fallback wheel may also exist), or COMPILE (only an sdist or wheels for other platforms: needs a compiler on the box).
glibc: the manylinux tag is printed; Amazon Linux 2023 has glibc 2.34 (manylinux_2_34 and older work).
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import re
import sys
import urllib.request

WHEEL = re.compile(r"^(?P<name>.+?)-(?P<ver>[^-]+)(-(?P<build>\d[^-]*))?-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$")


def fetch(name: str, version: str) -> dict:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def classify(files: list[str], arch: str, py: int) -> tuple[str, list[str]]:
    pure, native = [], []
    for fn in files:
        m = WHEEL.match(fn)
        if not m:
            continue
        pys, abi, plat = m["py"].split("."), m["abi"], m["plat"]
        if plat == "any" and any(p.startswith("py3") for p in pys):
            pure.append(fn)
            continue
        tags_ok = f"cp3{py}" in pys or (abi == "abi3" and any(
            p.startswith("cp3") and p[3:].isdigit() and int(p[3:]) <= py for p in pys))
        if tags_ok and any(arch in p and ("manylinux" in p or "musllinux" in p) for p in plat.split(".")):
            native.append(fn)
    if native:  # pip prefers the more specific native wheel when both exist
        return ("WHEEL", native + pure[:1]) if pure else ("WHEEL", native)
    if pure:
        return "PURE", pure[:1]
    return "COMPILE", []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="aarch64")
    ap.add_argument("--py", type=int, default=312)
    args = ap.parse_args()
    py = args.py % 100  # 312 -> minor version 12
    bad = 0
    print(f"{'package':<24}{'version':<10}verdict   evidence (wheel file)")
    for d in sorted(md.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name, ver = d.metadata["Name"], d.version
        try:
            data = fetch(name, ver)
        except Exception as e:  # noqa: BLE001
            print(f"{name:<24}{ver:<10}UNKNOWN   could not fetch PyPI metadata: {e}")
            bad += 1
            continue
        files = [u["filename"] for u in data["urls"]]
        verdict, ev = classify(files, args.platform, py)
        if verdict == "COMPILE":
            bad += 1
        print(f"{name:<24}{ver:<10}{verdict:<10}{', '.join(ev) if ev else '; '.join(files)[:200]}")
    print(f"\nNeeds compiling or unknown: {bad}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
