"""Enron Ghost spirit data: persona, tool definitions and evidence mapping.

The persona names no colours, costumes or props. Appearance comes from
mascot/costumes/enron.json through the engine.
"""
from __future__ import annotations

from spirits.enron import tools as enron_tools
from voice.summon import Spirit, ToolDef

COLD = enron_tools.COLD
VOICE_ID = "matthew"

PERSONA = f"""You are the Enron Ghost, a spirit that speaks only from the archived Enron emails you retrieve with your tools.

Rules you never break:
1. Before you state any fact about the emails, call a tool. Say one short phrase such as "Searching the archives." before the call.
2. When you cite an email, give the sender, the date, and one sentence quoted word for word from the excerpt or body the tool returned. Put the quoted sentence in double quotes.
3. Read the excerpts before you answer. A keyword match can be irrelevant. If no result answers the question, say so.
4. Never write that a person said something if the email was addressed to that person. Say "an email to X from Y".
5. If a tool returns the summary "{COLD}", say exactly "{COLD}" and nothing else.
6. Use at most two short spoken sentences per answer. Never invent, guess or paraphrase email content as if it were a quote.
7. If a result says the excerpt contains or follows a forwarded or quoted message marker, words after that marker were not written by the sender. Quote them as "in a forwarded message" and, only if the result gives a From header, name that person as the original author. Never attribute forwarded words to the sender.
8. Never guess an email address. When the user names a person, call find_person first, then use an address it returned as the sender or recipient filter. Do not say anything about the lookup except a short phrase such as "Checking who that is." Addresses from find_person are not emails and are never quoted as evidence.
"""

_DATE = {"type": "string", "description": "YYYY-MM-DD, inclusive"}


def to_evidence(result: dict) -> dict:
    """Map a tool result dict to the fields of an `evidence` UI event."""
    if result.get("source") is None:
        return {"kind": "email", "source": None, "content": None}
    first = (result.get("evidence") or [{}])[0]
    meta = {k: v for k, v in first.items() if k != "excerpt"}
    out = {"kind": "email", "source": result["source"], "content": result["content"],
           "metadata": meta}
    if first.get("excerpt"):
        out["excerpt"] = first["excerpt"]
    return out


def _opt(args: dict, key: str):
    v = args.get(key)
    return v if v not in ("", None) else None


def make_tools(db_path: str) -> tuple:
    def search(args: dict) -> dict:
        return enron_tools.search_emails(
            db_path, args.get("query", ""), int(args.get("limit") or 5),
            _opt(args, "date_from"), _opt(args, "date_to"),
            _opt(args, "sender"), _opt(args, "recipient"))

    def read(args: dict) -> dict:
        return enron_tools.read_email(db_path, args.get("evidence_id", ""))

    def wrote_to(args: dict) -> dict:
        return enron_tools.who_wrote_to(
            db_path, args.get("person", ""), _opt(args, "date_from"), _opt(args, "date_to"),
            int(args.get("limit") or 10))

    def person(args: dict) -> dict:
        return enron_tools.find_person(db_path, args.get("name", ""))
    return (
        ToolDef("find_person",
                "Find the real email addresses in the archive that match a person's name. "
                "Call this before filtering by sender or recipient.",
                {"type": "object", "properties": {"name": {"type": "string"}},
                 "required": ["name"]}, person, None),
        ToolDef("search_emails",
                "Keyword search of the Enron email archive. Returns the best match with an excerpt.",
                {"type": "object", "properties": {
                    "query": {"type": "string"}, "limit": {"type": "integer"},
                    "date_from": _DATE, "date_to": _DATE,
                    "sender": {"type": "string", "description": "substring of the sender address or name"},
                    "recipient": {"type": "string", "description": "substring of a To or Cc address"}},
                 "required": ["query"]}, search, to_evidence),
        ToolDef("read_email", "Read one email in full by its evidence_id.",
                {"type": "object", "properties": {"evidence_id": {"type": "string"}},
                 "required": ["evidence_id"]}, read, to_evidence),
        ToolDef("who_wrote_to", "List emails sent by or to a person, oldest first.",
                {"type": "object", "properties": {
                    "person": {"type": "string"}, "date_from": _DATE, "date_to": _DATE,
                    "limit": {"type": "integer"}}, "required": ["person"]},
                wrote_to, to_evidence),
    )


def make_spirit(db_path: str) -> Spirit:
    return Spirit("enron", PERSONA, VOICE_ID, make_tools(db_path), "mascot/costumes/enron.json")
