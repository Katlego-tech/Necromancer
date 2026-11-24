"""Checks finished spirit speech against what tools returned (grounding rule, level c)."""
from __future__ import annotations

import re

COLD = "The trail goes cold."
MIN_QUOTE_WORDS = 5
_QUOTE_RE = re.compile(r'"([^"]+)"|\u201c([^\u201d]+)\u201d')


def normalize(text: str) -> str:
    return " ".join(text.split())


def is_cold(result: dict) -> bool:
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
    return problems
