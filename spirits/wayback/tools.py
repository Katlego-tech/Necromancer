"""Wayback Medium tools. Every tool returns a ToolResult dict:

    {"source": <archive URL with 14-digit timestamp or None>, "content": <raw evidence or None>,
     "summary": <str>, "evidence": [<metadata dict>, ...]}

Nothing here invents content. Snapshot text is the archived page stripped of markup; the
timestamp reported is the one the archive actually served (it may differ from the one
requested). Network failures, HTTP errors and rate limiting yield the cold-trail result.
Network access and sleeping are injectable so tests run offline and instantly.

`fetch(url)` must return `(status:int, final_url:str, body:bytes)` and may raise OSError.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Callable, List, Optional, Tuple

COLD = "The trail goes cold."
CDX_URL = "http://web.archive.org/cdx/search/cdx"
SNAPSHOT_URL = "https://web.archive.org/web/{timestamp}id_/{url}"
USER_AGENT = ("Necromancer/0.1 (Kiroween 2026 hackathon voice agent; "
              "read-only research use of the Wayback Machine)")
DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "wayback")
SPARSE_CHARS = 200
SPARSE_LINES = 3
LABEL_SHARE = 0.6
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 20

_TS = re.compile(r"^\d{14}$")
_YEAR = re.compile(r"^\d{4}$")
_SERVED = re.compile(r"^https?://web\.archive\.org/web/(\d{14})[a-z_]*/(.+)$")

Fetch = Callable[[str], Tuple[int, str, bytes]]


def _cold() -> dict:
    return {"source": None, "content": None, "summary": COLD, "evidence": []}


def _check_url(url: str) -> str:
    url = (url or "").strip() if isinstance(url, str) else ""
    if re.match(r"^(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?::\d+)?(?:[/?#]|$)", url, re.I):
        url = "http://" + url  # a spoken site name such as "pets.com"
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.netloc or re.search(r"\s", url):
        raise ValueError(f"url must be an absolute http(s) URL, got {url!r}")
    return url


def _check_timestamp(ts: str) -> str:
    if not isinstance(ts, str) or not _TS.match(ts):
        raise ValueError(f"timestamp must be 14 digits (YYYYMMDDhhmmss), got {ts!r}")
    return ts


def _check_year(year) -> str:
    y = str(year).strip() if year is not None else ""
    if not _YEAR.match(y) or not 1996 <= int(y) <= 2100:
        raise ValueError(f"year must be a 4-digit year from 1996, got {year!r}")
    return y


def _default_fetch(url: str) -> Tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.geturl(), resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() or url, b""


def _get(url: str, fetch: Fetch, sleep: Callable[[float], None]) -> Optional[Tuple[int, str, bytes]]:
    """GET with polite exponential backoff on 429/5xx/network errors. None on failure."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            status, final_url, body = fetch(url)
        except Exception:  # timeouts, DNS, connection resets: treat as transient
            status, final_url, body = None, url, b""
        if status is not None and status not in RETRY_STATUSES:
            return status, final_url, body
        if attempt < MAX_ATTEMPTS - 1:
            sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------- cache

def _cache_path(cache_dir: str, *parts: str) -> str:
    key = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, key + ".json")


def _site_key(url: str) -> str:
    """Cache key for a page address: host without "www." and default ports, plus path and query.
    "pets.com", "http://www.pets.com/" and "https://pets.com:80" share one listing."""
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host + (p.path or "/") + ("?" + p.query if p.query else "")


def _alias_path(cache_dir: str, url: str, ts: str) -> str:
    """Second key for a stored snapshot: the same capture timestamp of the same registrable
    domain and path, whatever sub-host the caller or the listing named."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower().split(".")
    return _cache_path(cache_dir, "snap-alias", ".".join(host[-2:]), urllib.parse.urlsplit(url).path or "/", ts)


def _cache_read(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _cache_write(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass  # caching is best effort


# ---------------------------------------------------------------- html

class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template", "svg"}
    _BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4",
              "h5", "h6", "title", "hr", "pre", "blockquote", "form", "center", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(re.sub(r"[\s\xa0]+", " ", data))


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    lines = (ln.strip() for ln in "".join(p.parts).split("\n"))
    return "\n".join(ln for ln in lines if ln)


def _decode(body: bytes, truncated: bool = False) -> str:
    if truncated:  # the cut may split a multi-byte character; drop the partial tail
        for drop in range(4):
            try:
                return (body[:len(body) - drop] if drop else body).decode("utf-8")
            except UnicodeDecodeError:
                continue
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


# ---------------------------------------------------------------- tools

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


def spoken_date(timestamp) -> Optional[str]:
    """Deterministic spoken date from a 14-digit archive timestamp, e.g. "June 17, 2000".

    The timestamp is UTC, as the archive stores it. Returns None for anything that is not 14
    digits or not a real calendar date (the 14-digit value is still reported unchanged).
    """
    if not isinstance(timestamp, str) or not _TS.match(timestamp):
        return None
    year, month, day = int(timestamp[:4]), int(timestamp[4:6]), int(timestamp[6:8])
    try:
        datetime.date(year, month, day)
    except ValueError:
        return None
    return f"{_MONTHS[month - 1]} {day}, {year}"


def spoken_site(url) -> Optional[str]:
    """Host name as spoken, without a leading "www." (http://www.pets.com/ -> pets.com)."""
    host = urllib.parse.urlsplit(url).hostname if isinstance(url, str) else None
    if not host:
        return None
    return host[4:] if host.startswith("www.") and len(host) > 4 else host


def is_sparse(text: str) -> bool:
    """True when archived text is too thin to answer a question: under 200 characters, or fewer
    than three lines of at least five words (a page that is mostly navigation labels)."""
    if len(text) < SPARSE_CHARS:
        return True
    return sum(1 for ln in text.split("\n") if len(ln.split()) >= 5) < SPARSE_LINES


def is_label_heavy(text: str) -> bool:
    """True when at least 60% of the lines are three words or fewer (menus and link lists)."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return bool(lines) and sum(1 for ln in lines if len(ln.split()) <= 3) / len(lines) >= LABEL_SHARE


def _archive_link(url: str, timestamp: str) -> str:
    return f"https://web.archive.org/web/{timestamp}/{url}"


def _list_snapshots(url: str, year, *, cache_dir: str = DEFAULT_CACHE_DIR,
                   fetch: Optional[Fetch] = None,
                   sleep: Callable[[float], None] = time.sleep) -> dict:
    """List archived captures of `url` during `year` (one per month, HTTP 200, HTML)."""
    url, year = _check_url(url), _check_year(year)
    path = _cache_path(cache_dir, "cdx", _site_key(url), year)
    cached = _cache_read(path) or _cache_read(_cache_path(cache_dir, "cdx", url, year))  # older key
    rows = cached.get("snapshots") if cached else None
    if isinstance(rows, list):
        rows = [r for r in rows if isinstance(r, dict) and _TS.match(str(r.get("timestamp", "")))
                and isinstance(r.get("original"), str) and r["original"]]
    if not isinstance(rows, list) or not rows:
        rows = None
    if rows is None:
        query = urllib.parse.urlencode([
            ("url", url), ("from", year + "0101"), ("to", year + "1231"),
            ("output", "json"), ("fl", "timestamp,original,mimetype,statuscode"),
            ("filter", "statuscode:200"), ("filter", "mimetype:text/html"),
            ("collapse", "timestamp:6"), ("limit", "100")])
        got = _get(CDX_URL + "?" + query, fetch or _default_fetch, sleep)
        if got is None or got[0] != 200:
            return _cold()
        try:
            data = json.loads(got[2].decode("utf-8") or "[]")
        except ValueError:
            return _cold()
        rows = []
        if isinstance(data, list) and data and isinstance(data[0], list):
            head = data[0]
            for r in data[1:]:
                if not isinstance(r, list) or len(r) != len(head):
                    continue
                rec = dict(zip(head, r))
                if _TS.match(str(rec.get("timestamp", ""))):
                    rows.append({"timestamp": rec["timestamp"], "original": rec.get("original", url),
                                 "mimetype": rec.get("mimetype"), "statuscode": rec.get("statuscode")})
        if rows:  # never cache an empty answer
            _cache_write(path, {"url": url, "year": year, "snapshots": rows})
    if not rows:
        return _cold()
    evidence = [dict(r, archive_url=_archive_link(r["original"], r["timestamp"]),
                     spoken_date=spoken_date(r["timestamp"]),
                     spoken_site=spoken_site(r["original"])) for r in rows]
    content = "\n".join(f"{e['timestamp']} {e['spoken_date'] or ''} {e['original']} {e['archive_url']}"
                        .replace("  ", " ") for e in evidence)
    first, last = evidence[0], evidence[-1]
    dated = lambda e: e["spoken_date"] or f"timestamp {e['timestamp']}"  # noqa: E731
    return {
        "source": evidence[0]["archive_url"],
        "content": content,
        "summary": (f"Found {len(rows)} archived snapshot(s) of {rows[0]['original']} "
                    f"in {first['timestamp'][:4]}; "
                    f"first captured {dated(first)}, last captured {dated(last)}."),
        "evidence": evidence,
    }


def _read_snapshot(url: str, timestamp: str, *, cache_dir: str = DEFAULT_CACHE_DIR,
                  fetch: Optional[Fetch] = None,
                  sleep: Callable[[float], None] = time.sleep) -> dict:
    """Read the archived page nearest `timestamp` as plain text. Reports the served timestamp."""
    url, timestamp = _check_url(url), _check_timestamp(timestamp)
    req_path = _cache_path(cache_dir, "snap", url, timestamp)
    rec = _cache_read(req_path)
    alias = _alias_path(cache_dir, url, timestamp)
    if rec is None:
        rec = _cache_read(alias)
    elif _cache_read(alias) is None:
        _cache_write(alias, rec)  # cache written before aliases existed
    if rec is not None and rec.get("empty") is True:  # served before, had no readable text
        return _cold()
    cached = (rec is not None and _TS.match(str(rec.get("timestamp", ""))) is not None
              and isinstance(rec.get("url"), str) and bool(rec["url"])
              and isinstance(rec.get("text"), str) and bool(rec["text"]))
    if not cached:
        got = _get(SNAPSHOT_URL.format(timestamp=timestamp, url=url), fetch or _default_fetch, sleep)
        if got is None or got[0] != 200 or not got[2]:
            return _cold()
        m = _SERVED.match(got[1])
        if not m:  # cannot tell which capture was served, so cannot cite it
            return _cold()
        served_ts, served_url = m.group(1), m.group(2)
        truncated = len(got[2]) > MAX_BYTES
        text = html_to_text(_decode(got[2][:MAX_BYTES], truncated))
        if not text:  # a real capture with no readable text (e.g. a redirect page): remember that
            _cache_write(req_path, {"url": served_url, "timestamp": served_ts, "requested_url": url,
                                    "requested_timestamp": timestamp, "text": "", "empty": True})
            return _cold()
        rec = {"url": served_url, "timestamp": served_ts, "requested_url": url,
               "requested_timestamp": timestamp, "text": text}
        if truncated:
            rec["truncated"] = True
        _cache_write(req_path, rec)
        _cache_write(_cache_path(cache_dir, "snap", served_url, served_ts), rec)
        _cache_write(_alias_path(cache_dir, served_url, served_ts), rec)
    served_ts, served_url = rec["timestamp"], rec["url"]
    link = _archive_link(served_url, served_ts)
    said = spoken_date(served_ts)
    summary = (f"Archived snapshot of {served_url} captured on {said} (archive timestamp {served_ts})."
               if said else f"Archived snapshot of {served_url} captured at {served_ts}.")
    meta = {"url": served_url, "timestamp": served_ts, "spoken_date": said,
            "spoken_site": spoken_site(served_url),
            "requested_timestamp": rec.get("requested_timestamp", timestamp),
            "archive_url": link, "cached": bool(cached)}
    if is_sparse(rec["text"]):
        meta["sparse"] = True
        summary += (f" The archived text is sparse ({len(rec['text'])} characters, mostly short "
                    f"labels or very little text), so it can only be read out, not summarised.")
    elif is_label_heavy(rec["text"]):
        meta["label_heavy"] = True
        summary += (" Most lines of the archived text are short labels, so the page may be mostly "
                    "menus and links; read out only its readable sentences.")
    if rec.get("truncated"):
        meta["truncated"] = True
        summary += " The archived page was larger than the size limit, so this text is cut short."
    return {"source": link, "content": rec["text"], "summary": summary, "evidence": [meta],
            "spoken_site": meta["spoken_site"], "spoken_date": said}


def list_snapshots(url: str, year, *, cache_dir: str = DEFAULT_CACHE_DIR,
                   fetch: Optional[Fetch] = None,
                   sleep: Callable[[float], None] = time.sleep) -> dict:
    """Never raises: malformed input and unexpected errors return the cold trail."""
    try:
        return _list_snapshots(url, year, cache_dir=cache_dir, fetch=fetch, sleep=sleep)
    except Exception:
        return _cold()


def read_snapshot(url: str, timestamp: str, *, cache_dir: str = DEFAULT_CACHE_DIR,
                  fetch: Optional[Fetch] = None,
                  sleep: Callable[[float], None] = time.sleep) -> dict:
    """Never raises: malformed input and unexpected errors return the cold trail."""
    try:
        return _read_snapshot(url, timestamp, cache_dir=cache_dir, fetch=fetch, sleep=sleep)
    except Exception:
        return _cold()
