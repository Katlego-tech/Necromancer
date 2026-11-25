"""Derive a Wayback costume palette from the raw HTML of an archived page (stdlib only).

    python -m mascot.tools.snapshot_palette RAW_HTML --url URL --timestamp TS --write mascot/costumes/wayback.json

Colours come from bgcolor/text/link/vlink/alink attributes, inline style and <style> blocks
(hex, rgb() and a small table of named colours). Pure black and white are structural, not
distinctive, so they are ignored. Near duplicates are merged. 2 to 4 colours are kept.
Limits: no external stylesheets, images, or computed styles; only colours written in the HTML.
Returns None when the page does not give two distinct usable colours besides the body.
"""
from __future__ import annotations
import argparse
import colorsys
import json
import re
import sys
from collections import Counter
from html.parser import HTMLParser

FALLBACK_BODY = "#F3F0FF"
MIN_BODY_LUMINANCE = 0.75
NEAR = 40  # RGB euclidean distance under which two colours count as the same
NAMED = {
    "aqua": "00ffff", "black": "000000", "blue": "0000ff", "fuchsia": "ff00ff", "gray": "808080",
    "grey": "808080", "green": "008000", "lime": "00ff00", "maroon": "800000", "navy": "000080",
    "olive": "808000", "orange": "ffa500", "purple": "800080", "red": "ff0000", "silver": "c0c0c0",
    "teal": "008080", "white": "ffffff", "yellow": "ffff00", "pink": "ffc0cb", "brown": "a52a2a",
}
_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RGB = re.compile(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", re.I)
_WORD = re.compile(r"\b[a-zA-Z]+\b")
_CSS_DECL = re.compile(r"(?:^|[;{\s])(?:background-color|background|border-color|color)\s*:\s*([^;}]+)", re.I)
_ATTRS = {"bgcolor", "text", "link", "vlink", "alink", "color"}


def _to_rgb(h: str):
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _colours_in(value: str):
    """Yield 6-digit lowercase hex colours found in an attribute or CSS value."""
    for m in _HEX.finditer(value):
        h = m.group(1).lower()
        yield h if len(h) == 6 else "".join(c * 2 for c in h)
    for m in _RGB.finditer(value):
        r, g, b = (min(255, int(x)) for x in m.groups())
        yield f"{r:02x}{g:02x}{b:02x}"
    stripped = _HEX.sub(" ", value)
    for w in _WORD.findall(stripped):
        if w.lower() in NAMED:
            yield NAMED[w.lower()]


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []
        self._style = False

    def _css(self, text: str):
        for m in _CSS_DECL.finditer(text):
            self.found.extend(_colours_in(m.group(1)))

    def handle_starttag(self, tag, attrs):
        if tag == "style":
            self._style = True
        for k, v in attrs:
            if not v:
                continue
            if k.lower() in _ATTRS:
                self.found.extend(_colours_in(v))
            elif k.lower() == "style":
                self._css(v)

    def handle_endtag(self, tag):
        if tag == "style":
            self._style = False

    def handle_data(self, data):
        if self._style:
            self._css(data)


def _luminance(h: str) -> float:
    r, g, b = (c / 255 for c in _to_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _saturation(h: str) -> float:
    r, g, b = (c / 255 for c in _to_rgb(h))
    return colorsys.rgb_to_hsv(r, g, b)[1]


def _dist(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_to_rgb(a), _to_rgb(b))) ** 0.5


def extract(html: str):
    """Return {"colours": [#hex, ...2-4], "palette": {body, trim, eyes}} or None."""
    c = _Collector()
    try:
        c.feed(html)
        c.close()
    except Exception:
        pass
    counts = Counter(h for h in c.found if h not in ("000000", "ffffff"))
    merged: list[list] = []  # [hex, count], most frequent first wins its neighbours
    for h, n in counts.most_common():
        for m in merged:
            if _dist(h, m[0]) < NEAR:
                m[1] += n
                break
        else:
            merged.append([h, n])
    merged.sort(key=lambda m: -m[1])
    cols = ["#" + m[0].upper() for m in merged[:4]]
    light = [x for x in cols if _luminance(x[1:]) >= MIN_BODY_LUMINANCE]
    body = max(light, key=lambda x: _luminance(x[1:])) if light else FALLBACK_BODY
    rest = [x for x in cols if x != body]
    sat = [x for x in rest if _saturation(x[1:]) >= 0.25]
    ordered = sat + [x for x in rest if x not in sat]
    if len(ordered) < 2:
        return None
    return {"colours": cols, "palette": {"body": body, "trim": ordered[0], "eyes": ordered[1]}}


def build_costume(result, url: str, timestamp: str) -> dict:
    cfg = {"spirit": "wayback", "base": "little_necromancer", "renderer": "2d", "costume": "retro_web_era"}
    if result:
        cfg["label"] = f"Wayback Medium (palette from {timestamp})"
        cfg["palette"] = result["palette"]
    else:
        cfg["label"] = "Wayback Medium (era-inspired)"
        cfg["palette"] = {"body": FALLBACK_BODY, "trim": "#8A6ED6", "eyes": "#7DF9FF"}
    cfg.update(props=["browser_halo", "pixel_accents"], animation="shapeshift", reduced_motion=False)
    if result:
        cfg["snapshot"] = {"url": url, "timestamp": timestamp}
    else:
        cfg["era_inspired"] = True
    return cfg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw_html")
    ap.add_argument("--url", required=True)
    ap.add_argument("--timestamp", required=True)
    ap.add_argument("--write", help="costume JSON path to write")
    a = ap.parse_args(argv)
    with open(a.raw_html, "rb") as f:
        raw = f.read()
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("cp1252", errors="replace")
    res = extract(html)
    cfg = build_costume(res, a.url, a.timestamp)
    print(json.dumps({"extracted": res, "costume": cfg}, indent=2))
    if a.write:
        from mascot import appearance
        errs = appearance.validate(cfg)
        if errs:
            print("invalid costume:", errs, file=sys.stderr)
            return 1
        with open(a.write, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
