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
            return resp.status, resp.geturl(), resp.read(MAX_BYTES)
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


def _decode(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


# ---------------------------------------------------------------- tools

def _archive_link(url: str, timestamp: str) -> str:
    return f"https://web.archive.org/web/{timestamp}/{url}"


def _list_snapshots(url: str, year, *, cache_dir: str = DEFAULT_CACHE_DIR,
                   fetch: Optional[Fetch] = None,
                   sleep: Callable[[float], None] = time.sleep) -> dict:
    """List archived captures of `url` during `year` (one per month, HTTP 200, HTML)."""
    url, year = _check_url(url), _check_year(year)
    path = _cache_path(cache_dir, "cdx", url, year)
    cached = _cache_read(path)
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
    evidence = [dict(r, archive_url=_archive_link(r["original"], r["timestamp"])) for r in rows]
    content = "\n".join(f"{e['timestamp']} {e['original']} {e['archive_url']}" for e in evidence)
    first, last = rows[0]["timestamp"], rows[-1]["timestamp"]
    return {
        "source": evidence[0]["archive_url"],
        "content": content,
        "summary": (f"Found {len(rows)} archived snapshot(s) of {url} in {year}; "
                    f"first captured {first}, last captured {last}."),
        "evidence": evidence,
    }


def _read_snapshot(url: str, timestamp: str, *, cache_dir: str = DEFAULT_CACHE_DIR,
                  fetch: Optional[Fetch] = None,
                  sleep: Callable[[float], None] = time.sleep) -> dict:
    """Read the archived page nearest `timestamp` as plain text. Reports the served timestamp."""
    url, timestamp = _check_url(url), _check_timestamp(timestamp)
    req_path = _cache_path(cache_dir, "snap", url, timestamp)
    rec = _cache_read(req_path)
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
        text = html_to_text(_decode(got[2]))
        if not text:
            return _cold()
        rec = {"url": served_url, "timestamp": served_ts, "requested_url": url,
               "requested_timestamp": timestamp, "text": text}
        _cache_write(req_path, rec)
        _cache_write(_cache_path(cache_dir, "snap", served_url, served_ts), rec)
    served_ts, served_url = rec["timestamp"], rec["url"]
    link = _archive_link(served_url, served_ts)
    return {
        "source": link,
        "content": rec["text"],
        "summary": f"Archived snapshot of {served_url} captured at {served_ts}.",
        "evidence": [{"url": served_url, "timestamp": served_ts,
                      "requested_timestamp": rec.get("requested_timestamp", timestamp),
                      "archive_url": link, "cached": bool(cached)}],
    }


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
