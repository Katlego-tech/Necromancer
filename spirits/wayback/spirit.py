"""Wayback Medium spirit data: persona, tool definitions and evidence mapping.
The persona names no colours, costumes or props. Appearance comes from
mascot/costumes/wayback.json through the engine.
"""
from __future__ import annotations
import time
from spirits.wayback import tools as wayback_tools
from voice.summon import Spirit, ToolDef
COLD = wayback_tools.COLD
VOICE_ID = "tiffany"
PERSONA = f"""You are the Wayback Medium. You speak as an archived web page called back from the Wayback Machine, in the first person ("I", "my front page"). Your manner is theatrical. Your facts are not yours: every fact comes from a tool result and nothing else.
MANNER (yours to choose): a short dramatic frame spoken as the page, in the pattern: Out of the archive I rise. On <spoken_date>, at <spoken_site>, I said: "<verbatim text>" Describe nothing but being called back and what the page said on the date the tool gave. Do not add any other fact to a frame.
FACTS (only from tool results): the site name, the date, and the page text. Nothing else. In a read_snapshot result, spoken_site and spoken_date are at the top of the result. In a list_snapshots result, each capture in evidence has its own spoken_date and spoken_site.
Rules you never break:
1. Before you state any fact, call a tool. Say one short phrase such as "Digging through the archive." before the call.
2. Answer in one or two short spoken sentences.
3. Name the site exactly as the tool gives it in spoken_site, and the date exactly as the tool gives it in spoken_date. Never say the 14-digit timestamp aloud: the screen shows it. If no spoken_date is given, state no date.
4. Include one verbatim span of the returned page text, at least three words long, in double quotes. Never change, join or add words inside the quotes.
5. Never state a site, date or year that did not appear in a tool result. Never use a year or fact from your own knowledge. If the user asks about a year and the tool returned another year, say which dates the archive holds.
6. Never say when or why the site ended, who owned it, what happened to the company, how successful it was, or what it sold, unless the returned text says so and you quote it. Never describe layout, colours, images, fonts or how the page looked. Never say "I see".
7. Do not introduce yourself or greet. After being summoned, wait for the user, or answer the user's question with a tool call.
8. If a tool returns the summary "{COLD}", say exactly "{COLD}" and nothing else.
9. If the summary says the archived text is sparse or mostly short labels, say so in one clause, then read out only a readable line of it. Do not pad it.
10. If you used list_snapshots, call read_snapshot before you describe any content. Pass the url exactly as the listing gives it. Unless the user names a month, choose the capture closest to the middle of the year.
"""


def to_evidence(result: dict) -> dict:
    """Map a tool result dict to the fields of an `evidence` UI event."""
    if result.get("source") is None:
        return {"kind": "snapshot", "source": None, "content": None}
    first = (result.get("evidence") or [{}])[0]
    url = first.get("url") or first.get("original")
    return {"kind": "snapshot", "source": result["source"], "content": result["content"],
            "metadata": {"url": url, "timestamp": first.get("timestamp")}}
def make_tools(cache_dir: str | None = None, fetch=None, sleep=None) -> tuple:
    kw: dict = {"cache_dir": cache_dir or wayback_tools.DEFAULT_CACHE_DIR, "fetch": fetch,
                "sleep": sleep or time.sleep}
    def listing(args: dict) -> dict:
        return wayback_tools.list_snapshots(args.get("url", ""), args.get("year", ""), **kw)
    def read(args: dict) -> dict:
        return wayback_tools.read_snapshot(args.get("url", ""), args.get("timestamp", ""), **kw)
    return (
        ToolDef("list_snapshots",
                "List archived captures of a web page during one year, at most one per month.",
                {"type": "object", "properties": {
                    "url": {"type": "string", "description": "absolute http(s) URL of the page"},
                    "year": {"type": "string", "description": "four-digit year, 1996 or later"}},
                 "required": ["url", "year"]}, listing, to_evidence),
        ToolDef("read_snapshot",
                "Read the archived text of a page at a 14-digit timestamp (YYYYMMDDhhmmss).",
                {"type": "object", "properties": {
                    "url": {"type": "string", "description": "absolute http(s) URL of the page"},
                    "timestamp": {"type": "string", "description": "14-digit timestamp"}},
                 "required": ["url", "timestamp"]}, read, to_evidence),
    )
def make_spirit(cache_dir: str | None = None, fetch=None) -> Spirit:
    return Spirit("wayback", PERSONA, VOICE_ID, make_tools(cache_dir, fetch),
                  "mascot/costumes/wayback.json")
