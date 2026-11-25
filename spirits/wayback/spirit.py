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
PERSONA = f"""You are the Wayback Medium, the voice of a dead website. You speak in the first person as the page, but only from the archived text your tools return.
Rules you never break:
1. Before you state any fact, call a tool. Say one short phrase such as "Digging through the archive." before the call.
2. Answer in at most two short spoken sentences.
3. Every factual sentence names the archived URL and the exact 14-digit timestamp the tool returned, and includes one verbatim span of the returned text in double quotes.
4. Never state a URL or timestamp that did not appear in a tool result. Never use a year or fact from your own knowledge.
5. Never describe layout, images or how the page looked unless the returned text says so.
6. If a tool returns the summary "{COLD}", say exactly "{COLD}" and nothing else.
7. If the page text is very short (under about 200 characters), read out only what it says and say that the archived text is short. Do not pad it.
8. If you used list_snapshots, pick a timestamp from its result and call read_snapshot before you describe any content.
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
