"""Checks finished spirit speech against what tools returned (grounding rule, level c).

These checks run after a turn has been spoken. They flag, they do not prevent: the audio is
already out when a violation is recorded and emitted as an error event.
"""
from __future__ import annotations
import json
import re

COLD = "The trail goes cold."
MIN_QUOTE_WORDS = 3
_QUOTE_RE = re.compile(r'"([^"]+)"|\u201c([^\u201d]+)\u201d')
_TYPO = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                       "\u2013": "-", "\u2014": "-", "\u00a0": " "})
_EDGE_PUNCT = " \t\n.,;:!?-"


def normalize(text: str) -> str:
    return " ".join(text.split())


def _fold(text: str) -> str:
    """normalize plus typographic quotes and dashes mapped to ASCII (never changes letters)."""
    return normalize(text.translate(_TYPO))


_DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
           "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_DIGIT_WORDS_RE = re.compile(r"\b(?:%s)(?:[\s,.-]+(?:%s))+\b" % ("|".join(_DIGITS), "|".join(_DIGITS)), re.I)
_TS_RE = re.compile(r"(?<!\d)\d{14}(?!\d)")
_URL_RE = re.compile(r"https?://[^\s\"'<>)]+", re.I)
_HOST_RE = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:com|org|net|edu|gov|io|co|uk|info|biz))\b", re.I)
# A run of spoken digit words and/or numeral chunks (at least 8 digits) read as one number.
_DIGIT_TOKEN = r"(?:%s|\d+)" % "|".join(_DIGITS)
_NUMBER_RUN_RE = re.compile(r"(?<![\w.])%s(?:(?:[\s,.-]+(?:%s))|(?:[\s-]+\d+))*(?![\w])" % (
    _DIGIT_TOKEN, "|".join(_DIGITS)), re.I)


def decode_spoken_digits(text: str) -> str:
    """Replace runs of spoken digit words ("two zero zero zero") with digits."""
    def sub(m):
        return "".join(_DIGITS[w.lower()] for w in re.findall(r"[A-Za-z]+", m.group(0)))
    return _DIGIT_WORDS_RE.sub(sub, text)


def digit_runs(text: str, min_digits: int = 8) -> list[str]:
    """Digit strings of at least `min_digits` spoken as digit words and/or numeral chunks."""
    out = []
    for m in _NUMBER_RUN_RE.finditer(text):
        digits = "".join(_DIGITS[t.lower()] if t.lower() in _DIGITS else t
                         for t in re.findall(r"[A-Za-z]+|\d+", m.group(0)))
        if len(digits) >= min_digits:
            out.append(digits)
    return out


def is_cold(result: dict) -> bool:
    if "summoning" in result:  # summon_spirit result: carries no evidence, is not a cold trail
        return False
    return result.get("summary") == COLD or (
        result.get("source") is None and result.get("content") is None)


def _spans(text: str) -> list[str]:
    """Double-quoted spans, split on ellipses, trimmed; parts under MIN_QUOTE_WORDS are dropped."""
    spans = []
    for m in _QUOTE_RE.finditer(text):
        for part in re.split(r"\.\.\.|\u2026", m.group(1) or m.group(2)):
            part = part.strip(_EDGE_PUNCT)
            if len(part.split()) >= MIN_QUOTE_WORDS:
                spans.append(part)
    return spans


def quoted_spans(text: str) -> list[str]:
    return _spans(text)


def _unquoted(text: str) -> str:
    return _QUOTE_RE.sub(" ", text)


# ---------------------------------------------------------------- spoken dates
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]
_MONTH_RE = r"(?P<month>%s|sept)" % "|".join(_MONTHS)
_DATE_UNITS = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_DATE_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
          "eighteen", "nineteen"]
_DATE_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SMALL = r"(?:(?:%s)(?:[\s-]+(?:%s))?|%s|%s)" % ("|".join(_DATE_TENS), "|".join(_DATE_UNITS),
                                                    "|".join(_DATE_TEENS), "|".join(_DATE_UNITS))
_YEAR_WORDS_RE = re.compile(
    r"\b(?:two\s+thousand(?:\s+and)?(?:[\s-]+(?P<tail>%s))?"
    r"|(?P<cent>nineteen|twenty)[\s-]+(?:(?:oh|zero)[\s-]+(?P<ou>%s)|(?P<rest>(?:%s)(?:[\s-]+(?:%s))?|%s)))\b"
    % (_SMALL, "|".join(_DATE_UNITS), "|".join(_DATE_TENS), "|".join(_DATE_UNITS), "|".join(_DATE_TEENS)), re.I)
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
        "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
        "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
        "nineteenth": 19, "twentieth": 20, "thirtieth": 30}
for _i, _u in enumerate(["first", "second", "third", "fourth", "fifth", "sixth", "seventh",
                         "eighth", "ninth"], 1):
    _ORD["twenty-" + _u] = 20 + _i
    _ORD["twenty " + _u] = 20 + _i
_ORD["thirty-first"] = _ORD["thirty first"] = 31
_DAY_RE = r"(?P<day>\d{1,2}(?:st|nd|rd|th)?|%s)" % "|".join(
    re.escape(k).replace(r"\ ", r"[\s-]") for k in sorted(_ORD, key=len, reverse=True))
_YEAR_RE = r"(?P<year>(?:19|20)\d\d)"
_DATE_A = re.compile(r"\b%s(?:\s+(?:the\s+)?%s)?(?:,?\s+(?:of\s+)?%s)?\b" % (_MONTH_RE, _DAY_RE, _YEAR_RE), re.I)
_DATE_B = re.compile(r"\b(?:the\s+)?%s\s+of\s+%s(?:,?\s+%s)?\b" % (_DAY_RE, _MONTH_RE, _YEAR_RE), re.I)
_BARE_YEAR_RE = re.compile(r"\b(?:in|from|during|since|until|circa|of|year)\s+(?P<year>(?:19|20)\d\d)\b", re.I)


def _word_num(s: str) -> int:
    total = 0
    for w in re.findall(r"[a-z]+", s.lower()):
        if w in _DATE_UNITS:
            total += _DATE_UNITS.index(w) + 1
        elif w in _DATE_TEENS:
            total += 10 + _DATE_TEENS.index(w)
        elif w in _DATE_TENS:
            total += 20 + 10 * _DATE_TENS.index(w)
    return total


def decode_spoken_years(text: str) -> str:
    """Replace spoken years ("nineteen ninety-nine", "two thousand and one") with four digits."""
    def sub(m):
        if m.group("cent"):
            base = 1900 if m.group("cent").lower() == "nineteen" else 2000
            return str(base + (_word_num(m.group("ou")) if m.group("ou") else _word_num(m.group("rest"))))
        return str(2000 + (_word_num(m.group("tail")) if m.group("tail") else 0))
    return _YEAR_WORDS_RE.sub(sub, text)


def _day_value(raw: str | None) -> int | None:
    if not raw:
        return None
    raw = raw.lower()
    if raw[0].isdigit():
        return int(re.match(r"\d+", raw).group(0))
    return _ORD.get(re.sub(r"\s+", "-", raw)) or _ORD.get(raw)


def date_mentions(text: str) -> list[tuple[int, int | None, int | None]]:
    """(month, day, year) for every spoken or written date that names a month and a day or year.
    Month-only mentions ("in June") are not dates and are skipped."""
    text = decode_spoken_years(text)
    found = []
    taken: list[tuple[int, int]] = []
    for rx in (_DATE_B, _DATE_A):  # "17th of June, 2000" first, so the month-first form skips it
        for m in rx.finditer(text):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            name = m.group("month").lower()
            month = 9 if name == "sept" else _MONTHS.index(name) + 1
            day, year = _day_value(m.group("day")), (int(m.group("year")) if m.group("year") else None)
            if day is None and year is None:
                continue
            found.append((month, day, year))
    return found


def _allowed_dates(blob: str) -> set[tuple[int, int, int]]:
    out = set()
    for ts in _TS_RE.findall(blob):
        y, mo, d = int(ts[:4]), int(ts[4:6]), int(ts[6:8])
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.add((mo, d, y))
    return out


# ---------------------------------------------------------------- wayback manner/fact line
# Unquoted speech may carry manner but not facts the tool did not return. These words are
# claims about the site's fate, its business, or its looks; none of them comes from a tool.
_FATE_RE = re.compile(
    r"\b(bankrupt\w*|liquidat\w+|shut(?:ting)?\s+down|shut\s+its|went\s+(?:under|bust|dark|offline|"
    r"public|out\s+of\s+business)|out\s+of\s+business|dot[\s-]?com\s+(?:bubble|crash|bust|era|boom)|"
    r"collaps\w+|died|dying|perished|defunct|shuttered|discontinued|acquired|merged|merger|"
    r"founded|was\s+(?:bought|sold|closed|killed|launched)|ipo|investors?|stock\s+(?:price|market)|"
    r"disappear\w*|vanish\w*)\b", re.I)
_LOOK_RE = re.compile(
    r"\b(red|orange|yellow|green|blue|purple|pink|brown|gr[ae]y|black|white|gold|golden|silver|"
    r"colou?rs?|layout|banner|logo|fonts?|typeface|background|screenshot|graphics?|images?|"
    r"pictures?|photos?|buttons?|pixel\w*|looked|looks|appeared|sidebar|header|footer|"
    r"animated|flashing|blinking)\b", re.I)
_BIG_NUMBER_RE = re.compile(r"\b(million|billion|trillion)\b", re.I)


def wayback_style_problems(text: str) -> list[str]:
    plain = _unquoted(text)
    problems = []
    for m in _FATE_RE.finditer(plain):
        problems.append(f"claim about the site's fate or business not from a tool: {m.group(0)!r}")
    for m in _LOOK_RE.finditer(plain):
        problems.append(f"description of colour or layout, which tools never return: {m.group(0)!r}")
    for m in _BIG_NUMBER_RE.finditer(plain):
        problems.append(f"spoken large number cannot be matched to a timestamp: {m.group(0)!r}")
    return problems


def check_turn(text: str, tool_results: list[dict], after_cold: bool = False,
               spirit: str | None = None) -> list[str]:
    """Return a list of violation messages (empty when the turn is grounded)."""
    problems: list[str] = []
    if after_cold:
        if normalize(text) != COLD:
            problems.append(f"turn after a cold result must be exactly {COLD!r}, got {text!r}")
        return problems
    haystacks = [_fold(r["content"]) for r in tool_results if isinstance(r.get("content"), str)]
    for span in _spans(text):
        n = _fold(span)
        if not any(n in h for h in haystacks):
            problems.append(f"quoted text not found in any tool result: {span!r}")
    blob = " ".join(json.dumps(r) for r in tool_results)
    spoken = decode_spoken_digits(text)
    stamps = set(_TS_RE.findall(blob))
    flagged: set[str] = set()
    for ts in _TS_RE.findall(spoken):
        if ts not in blob:
            flagged.add(ts)
            problems.append(f"timestamp not found in any tool result: {ts!r}")
    allowed = _allowed_dates(blob)
    archive = spirit == "wayback" or bool(stamps)  # archive-style results: dates, hosts, long numbers
    for run in (digit_runs(text) if archive else []):
        if run not in flagged and not any(run in ts for ts in stamps):
            problems.append(f"spoken number not found in any tool result timestamp: {run!r}")
    for url in _URL_RE.findall(text):
        if url.rstrip(".,;") not in blob:
            problems.append(f"URL not found in any tool result: {url!r}")
    plain = _unquoted(text)
    blob_l = blob.lower()
    for host in (_HOST_RE.findall(_URL_RE.sub(" ", plain)) if archive else []):
        if host.lower() not in blob_l:
            problems.append(f"site name not found in any tool result: {host!r}")
    allowed_years = {y for (_, _, y) in allowed}
    for month, day, year in (date_mentions(plain) if archive else []):
        if not any(m == month and (day is None or d == day) and (year is None or y == year)
                   for (m, d, y) in allowed):
            said = " ".join(str(x) for x in (month, day, year) if x is not None)
            problems.append(f"spoken date not found in any tool result (month day year): {said}")
    if allowed:
        for m in _BARE_YEAR_RE.finditer(decode_spoken_years(plain)):
            if int(m.group("year")) not in allowed_years:
                problems.append(f"year not found in any tool result dates: {m.group('year')}")
    if spirit == "wayback":
        problems.extend(wayback_style_problems(text))
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
