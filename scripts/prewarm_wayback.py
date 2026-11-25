"""Pre-warm the Wayback Medium cache with real Wayback Machine requests (be polite).

    .venv/bin/python scripts/prewarm_wayback.py --probe
    .venv/bin/python scripts/prewarm_wayback.py --site http://www.altavista.com/ --timestamp 19980101000000

`--probe`  for each candidate: one list_snapshots and at most two read_snapshot calls, with
           >= 1 s between candidates' requests. Prints URL, served 14-digit timestamp, character
           count, first 300 characters and a usability verdict. Also saves the raw served HTML to
           data/wayback/raw/<timestamp>.html (one extra request) so palette colours can be extracted.
`--demo`   pre-warms the natural demo path ("Summon pets.com in 1999", "the front page of AltaVista in
           1997"): for each entry in DEMO_YEARS the year's capture listing (list_snapshots) and the
           text of up to DEMO_MAX_READS of the listed captures (read_snapshot). Idempotent: anything
           already in the cache costs no request, so a second run makes none. Polite: >= 1.5 s
           between network requests, no retries beyond the tools' own backoff.
`--site`   pre-warms exactly one snapshot (cache plus raw HTML) and prints URL, served timestamp
           and the sha256 of the text.
The cache directory is data/wayback/ (gitignored). Nothing here is committed.
"""
from __future__ import annotations
import argparse
import hashlib
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from spirits.wayback import tools  # noqa: E402

CANDIDATES = [  # (url, year, requested timestamps tried in order)
    ("http://www.pets.com/", "1999", ["19991101000000", "19990601000000"]),
    ("http://www.pets.com/", "2000", ["20000601000000", "20001101000000"]),
    ("http://www.altavista.com/", "1997", ["19971101000000", "19970601000000"]),
    ("http://www.altavista.com/", "1998", ["19980601000000", "19981101000000"]),
    ("http://www.geocities.com/", "1998", ["19980601000000", "19981101000000"]),
]
PAUSE = 1.5
DEMO_YEARS = [  # (url, year): the pages and years the demo script asks about
    ("http://www.pets.com/", "1999"),
    ("http://www.pets.com/", "2000"),
    ("http://www.pets.com/", "2001"),
    ("http://www.altavista.com/", "1997"),
]
DEMO_MAX_READS = 8


def save_raw(url: str, timestamp: str, cache_dir: str) -> str | None:
    """Fetch the raw served HTML once (same _get and default fetch) and save it by served timestamp."""
    got = tools._get(tools.SNAPSHOT_URL.format(timestamp=timestamp, url=url),
                     tools._default_fetch, time.sleep)
    if got is None or got[0] != 200 or not got[2]:
        return None
    m = tools._SERVED.match(got[1])
    if not m:
        return None
    raw_dir = os.path.join(cache_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    path = os.path.join(raw_dir, m.group(1) + ".html")
    with open(path, "wb") as f:
        f.write(got[2][:tools.MAX_BYTES])
    return path


def verdict(text: str) -> str:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n", text) if len(s.split()) >= 6]
    words = len(text.split())
    if words < 25 or len(sentences) < 3:
        return f"NOT USABLE: {words} words, {len(sentences)} readable sentence-like lines"
    return f"USABLE: {words} words, {len(sentences)} readable sentence-like lines"


def report(res: dict) -> bool:
    ev = res["evidence"][0] if res["evidence"] else None
    if not ev:
        print("  COLD (no usable snapshot)")
        return False
    text = res["content"]
    print(f"  URL: {ev['url']}\n  served timestamp: {ev['timestamp']} (requested {ev['requested_timestamp']})")
    print(f"  chars: {len(text)}\n  first 300: {text[:300]!r}\n  verdict: {verdict(text)}")
    return True


def probe(cache_dir: str) -> None:
    last_year_url = None
    for url, year, stamps in CANDIDATES:
        print(f"== {url} {year}")
        time.sleep(PAUSE)
        snaps = tools.list_snapshots(url, year, cache_dir=cache_dir, fetch=fetch, sleep=pause)
        if not snaps["evidence"]:
            print("  no captures listed")
            continue
        print(f"  {snaps['summary']}")
        # pick up to two captures present in the list, nearest the preferred months
        avail = [e["timestamp"] for e in snaps["evidence"]]
        picks = []
        for want in stamps:
            near = min(avail, key=lambda t: abs(int(t[:6]) - int(want[:6])))
            if near not in picks:
                picks.append(near)
        for ts in picks[:2]:
            time.sleep(PAUSE)
            res = tools.read_snapshot(url, ts, cache_dir=cache_dir)
            if report(res):
                time.sleep(PAUSE)
                served = res["evidence"][0]
                p = save_raw(url, ts, cache_dir)
                print(f"  raw html: {p}")
                break  # one successful read per candidate keeps us well under two


def _listing_cached(url: str, year: str, cache_dir: str) -> bool:
    """True when the listing is cached. A listing stored under the older exact-URL key is copied
    to the current site key (no request), so "pets.com" and "http://www.pets.com" find it."""
    new = tools._cache_path(cache_dir, "cdx", tools._site_key(url), year)
    if tools._cache_read(new) is not None:
        return True
    old = tools._cache_read(tools._cache_path(cache_dir, "cdx", url, year))
    if old is None:
        return False
    tools._cache_write(new, old)
    return True


def pick_reads(timestamps: list[str], limit: int = DEMO_MAX_READS) -> list[str]:
    """All listed captures, or `limit` of them spread evenly across the year when there are more."""
    if len(timestamps) <= limit:
        return list(timestamps)
    step = (len(timestamps) - 1) / (limit - 1)
    return [timestamps[round(i * step)] for i in range(limit)]


def demo(cache_dir: str, pause=time.sleep, fetch=None) -> int:
    """Pre-warm listings and snapshot texts for DEMO_YEARS. Returns the number of network-bound steps."""
    network_steps = 0
    for url, year in DEMO_YEARS:
        print(f"== {url} {year}")
        if not _listing_cached(url, year, cache_dir):
            pause(PAUSE)
            network_steps += 1
        snaps = tools.list_snapshots(url, year, cache_dir=cache_dir, fetch=fetch, sleep=pause)
        if not snaps["evidence"]:
            print("  no captures listed (the archive holds no HTML 200 capture that year)")
            continue
        print(f"  {snaps['summary']}")
        # Read with the URL the listing reports (`original`): the model is told to pass that URL
        # on to read_snapshot, and the cache is keyed on the exact URL and timestamp requested.
        original = {e["timestamp"]: e["original"] for e in snaps["evidence"]}
        for ts in pick_reads(list(original)):
            rurl = original[ts]
            have = (tools._cache_read(tools._cache_path(cache_dir, "snap", rurl, ts)) is not None
                    or tools._cache_read(tools._alias_path(cache_dir, rurl, ts)) is not None)
            if not have:
                pause(PAUSE)
                network_steps += 1
            res = tools.read_snapshot(rurl, ts, cache_dir=cache_dir, fetch=fetch, sleep=pause)
            ev = res["evidence"][0] if res["evidence"] else None
            if not ev:
                print(f"  {ts}: COLD (no usable snapshot)")
                continue
            note = " [sparse]" if ev.get("sparse") else ""
            print(f"  {ts} -> served {ev['timestamp']} ({ev['spoken_date']}), "
                  f"{len(res['content'])} chars{note}{'' if have else ' (fetched)'}")
    print(f"network-bound steps this run: {network_steps}")
    return network_steps


def site(url: str, timestamp: str, cache_dir: str) -> int:
    res = tools.read_snapshot(url, timestamp, cache_dir=cache_dir)
    if not report(res):
        return 1
    ev = res["evidence"][0]
    print(f"  sha256(text): {hashlib.sha256(res['content'].encode('utf-8')).hexdigest()}")
    time.sleep(PAUSE)
    print(f"  raw html: {save_raw(ev['url'], ev['timestamp'], cache_dir)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true", help="probe the candidate sites (real network)")
    ap.add_argument("--demo", action="store_true", help="pre-warm the demo listings and snapshots")
    ap.add_argument("--site", help="URL to pre-warm")
    ap.add_argument("--timestamp", help="14-digit timestamp to request for --site")
    ap.add_argument("--cache-dir", default=tools.DEFAULT_CACHE_DIR)
    a = ap.parse_args()
    if a.probe:
        probe(a.cache_dir)
        return 0
    if a.demo:
        demo(a.cache_dir)
        return 0
    if a.site and a.timestamp:
        return site(a.site, a.timestamp, a.cache_dir)
    ap.error("use --demo, --probe, or --site URL --timestamp TS")


if __name__ == "__main__":
    sys.exit(main())
