"""Checks finished spirit speech against what tools returned (grounding rule, level c)."""
from __future__ import annotations

import json
import re

COLD = "The trail goes cold."
MIN_QUOTE_WORDS = 5
_QUOTE_RE = re.compile(r'"([^"]+)"|\u201c([^\u201d]+)\u201d')


def normalize(text: str) -> str:
    return " ".join(text.split())


_DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
           "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DIGIT_WORDS_RE = re.compile(r"\b(?:%s)(?:[\s,.-]+(?:%s))+\b" % ("|".join(_DIGITS), "|".join(_DIGITS)), re.I)
_TS_RE = re.compile(r"(?<!\d)\d{14}(?!\d)")
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+", re.I)


def decode_spoken_digits(text: str) -> str:
    """Replace runs of spoken digit words ("two zero zero zero") with digits."""
    def sub(m):
        return "".join(_DIGITS[w.lower()] for w in re.findall(r"[A-Za-z]+", m.group(0)))
    return _DIGIT_WORDS_RE.sub(sub, text)


def is_cold(result: dict) -> bool:
    if "summoning" in result:  # summon_spirit result: carries no evidence, is not a cold trail
        return False
    return result.get("summary") == COLD or (
        result.get("source") is None and result.get("content") is None)


def quoted_spans(text: str) -> list[str]:
    spans = []
    for m in _QUOTE_RE.finditer(text):
        span = m.group(1) or m.group(2)
        if len(span.split()) >= MIN_QUOTE_WORDS:
            spans.append(span)
    return spans


def check_turn(text: str, tool_results: list[dict], after_cold: bool = False) -> list[str]:
    """Return a list of violation messages (empty when the turn is grounded)."""
    problems: list[str] = []
    if after_cold:
        if normalize(text) != COLD:
            problems.append(f"turn after a cold result must be exactly {COLD!r}, got {text!r}")
        return problems
    haystacks = [normalize(r["content"]) for r in tool_results
                 if isinstance(r.get("content"), str)]
    for span in quoted_spans(text):
        n = normalize(span)
        if not any(n in h for h in haystacks):
            problems.append(f"quoted text not found in any tool result: {span!r}")
    blob = " ".join(json.dumps(r) for r in tool_results)
    for ts in _TS_RE.findall(decode_spoken_digits(text)):
        if ts not in blob:
            problems.append(f"timestamp not found in any tool result: {ts!r}")
    for url in _URL_RE.findall(text):
        if url.rstrip(".,;") not in blob:
            problems.append(f"URL not found in any tool result: {url!r}")
    return problems
