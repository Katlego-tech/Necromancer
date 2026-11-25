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
_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_NUM_WORD = "|".join(sorted([*_ONES, *_TENS], key=len, reverse=True))
_NUM_RE = re.compile(r"\b(?:%s)(?:[\s-]+(?:%s))*\b" % (_NUM_WORD, _NUM_WORD), re.I)
def _words_to_values(words: list[str]) -> list[int]:
    """Greedy parse of a run of number words into numbers (tens+ones pair up, so
    "thirty one" is 31 and "twenty twenty six" is 20, 26)."""
    out: list[int] = []
    i = 0
    while i < len(words):
        w = words[i]
        if w in _TENS:
            v = _TENS[w]
            if i + 1 < len(words) and 1 <= _ONES.get(words[i + 1], 0) <= 9:
                v += _ONES[words[i + 1]]
                i += 1
            out.append(v)
        else:
            out.append(_ONES[w])
        i += 1
    return out
def normalize_number_words(text: str) -> str:
    """Replace spoken numbers with digits: "thirty one" -> "31", "twenty twenty six" -> "2026"."""
    def sub(m):
        vals = _words_to_values(re.findall(r"[a-z]+", m.group(0).lower()))
        if len(vals) == 2 and vals[0] in (19, 20) and 10 <= vals[1] <= 99:
            return str(vals[0] * 100 + vals[1])
        return " ".join(str(v) for v in vals)
    return _NUM_RE.sub(sub, text)
_SPOKEN_START_RE = re.compile(r"(\d{1,2})(?::(\d{2}))? (AM|PM) on (\d{1,2}) ([A-Za-z]+) (\d{4})$")
def check_alert(text: str, stage: str, area: str, spoken_start: str) -> list[str]:
    """Violations for a spoken Grid Ghost alert against the schedule record values."""
    problems: list[str] = []
    t = normalize_number_words(normalize(text)).lower()
    t = re.sub(r"[,.;:]", " ", t)
    t = " ".join(t.split())
    m = re.search(r"\bstage (\d+)\b", t)
    if not m:
        problems.append("alert does not state a stage")
    elif m.group(1) != str(stage):
        problems.append(f"spoken stage {m.group(1)} differs from record stage {stage}")
    base = area.split("(")[0].strip().lower()
    if base and base not in t:
        problems.append(f"area {base!r} missing from alert")
    am = re.search(r"\(([^)]*\d+[^)]*)\)", area)
    if am:
        want = re.sub(r"[,.;:]", " ", am.group(1).lower())
        want_n = re.search(r"demo area (\d+)", want)
        got_n = re.search(r"demo area (\d+)", t)
        if want_n and not got_n:
            problems.append("demo area number missing from alert")
        elif want_n and got_n.group(1) != want_n.group(1):
            problems.append(f"spoken demo area {got_n.group(1)} differs from record {want_n.group(1)}")
    sm = _SPOKEN_START_RE.match(spoken_start)
    if not sm:
        problems.append(f"cannot parse spoken start {spoken_start!r}")
        return problems
    hour, minute, ampm, day, month, year = sm.groups()
    tm = re.search(r"\b(\d{1,2})(?: (\d{2}))? ?(am|pm)\b", t)
    if not tm:
        problems.append("start hour and AM/PM missing from alert")
    else:
        if tm.group(1) != hour or tm.group(3) != ampm.lower():
            problems.append(f"spoken time {tm.group(0)!r} differs from record {hour} {ampm}")
        if minute and (tm.group(2) or "00") != minute:
            problems.append(f"spoken minutes differ from record {minute}")
        if not minute and tm.group(2) not in (None, "00"):
            problems.append("spoken minutes differ from record (none)")
    dm = re.search(r"\bon (\d{1,2}) ([a-z]+) (\d{4})\b", t)
    if not dm:
        problems.append("start date missing from alert")
    else:
        if dm.group(1) != day:
            problems.append(f"spoken day {dm.group(1)} differs from record {day}")
        if dm.group(2) != month.lower():
            problems.append(f"spoken month {dm.group(2)} differs from record {month}")
        if dm.group(3) != year:
            problems.append(f"spoken year {dm.group(3)} differs from record {year}")
    return problems
