"""Enron Ghost tools. Every tool returns a ToolResult dict:

    {"source": <evidence_id or None>, "content": <raw evidence or None>,
     "summary": <str>, "evidence": [<metadata dict>, ...]}

`evidence` entries carry: evidence_id, message_id, source_path, sender, recipients,
date, subject (and `excerpt` for searches). Nothing here invents content: summaries are
built only from database fields, and excerpts are verbatim slices of the stored body.
"""
from __future__ import annotations

import re
import sqlite3
from typing import List, Optional

COLD = "The trail goes cold."
_TOKEN = re.compile(r"[A-Za-z0-9_']+")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Markers that start a quoted or forwarded message inside a body. Text after one of these
# was written by someone other than the email's own sender.
_FWD_MARKER = re.compile(
    r"-{3,}\s*Original Message\s*-{3,}|-{5,}\s*Forwarded by [^\n]*", re.I)
_FWD_FROM = re.compile(r"^[ \t]*From:[ \t]*(.+?)[ \t]*$", re.I | re.M)


def _cold() -> dict:
    return {"source": None, "content": None, "summary": COLD, "evidence": []}


def _check_date(value: Optional[str], name: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if not _DATE.match(value):
        raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}")
    return value


def _fts_query(text: str) -> Optional[str]:
    """Turn free text into a safe FTS5 AND query of quoted tokens."""
    tokens = _TOKEN.findall(text or "")
    if not tokens:
        return None
    return " ".join('"%s"' % t for t in tokens)


def _like(value: str) -> str:
    return "%" + value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _forward_info(body: str, excerpt: str) -> dict:
    """Say whether the excerpt contains or follows a forwarded or quoted message marker.

    Returns {} when it does not (or the excerpt cannot be located). Otherwise returns
    forwarded=True, the verbatim marker text and, when the marker is followed by a
    "From:" header, that header value verbatim. Words after the marker were not written
    by the email's sender; words before it were. Nothing is inferred beyond these strings.
    """
    end = -1
    for piece in excerpt.split(" ... "):
        piece = piece.strip()
        if len(piece) >= 15:
            at = body.find(piece)
            if at >= 0:
                end = max(end, at + len(piece))
    if end < 0:
        return {}
    marker = None
    for m in _FWD_MARKER.finditer(body):
        if m.start() < end:
            marker = m
    if marker is None:
        return {}
    info = {"forwarded": True, "forward_marker": marker.group(0).strip()}
    header = _FWD_FROM.search(body, marker.end(), min(len(body), marker.end() + 400))
    if header and header.start() < end:
        info["forwarded_from"] = header.group(1).strip()
    return info


def _meta(r: sqlite3.Row, excerpt: Optional[str] = None) -> dict:
    m = {
        "evidence_id": r["evidence_id"],
        "message_id": r["message_id"],
        "source_path": r["source_path"],
        "sender": r["sender"],
        "recipients": r["recipients"],
        "date": r["date_iso"],
        "subject": r["subject"],
    }
    if excerpt is not None:
        m["excerpt"] = excerpt
        m.update(_forward_info(r["body"], excerpt))
    return m


def _date_clauses(date_from: Optional[str], date_to: Optional[str], col: str, params: list) -> str:
    sql = ""
    if date_from:
        sql += f" AND {col} >= ?"
        params.append(date_from)
    if date_to:
        sql += f" AND {col} < date(?, '+1 day')"
        params.append(date_to)
    return sql


def search_emails(db_path: str, query: str, limit: int = 5,
                  date_from: Optional[str] = None, date_to: Optional[str] = None,
                  sender: Optional[str] = None, recipient: Optional[str] = None) -> dict:
    """Keyword search. Dates are 'YYYY-MM-DD' (inclusive). `sender` and `recipient`
    are substring matches (recipient covers To and Cc)."""
    date_from, date_to = _check_date(date_from, "date_from"), _check_date(date_to, "date_to")
    q = _fts_query(query)
    if q is None:
        return _cold()
    params: list = [q]
    sql = ("SELECT e.*, snippet(emails_fts, 1, '', '', ' ... ', 24) AS excerpt "
           "FROM emails_fts JOIN emails e ON e.id = emails_fts.rowid "
           "WHERE emails_fts MATCH ?")
    if sender and sender.strip():
        sql += " AND e.sender LIKE ? ESCAPE '\\'"
        params.append(_like(sender.strip()))
    if recipient and recipient.strip():
        sql += " AND e.recipients LIKE ? ESCAPE '\\'"
        params.append(_like(recipient.strip()))
    sql += _date_clauses(date_from, date_to, "e.date_iso", params)
    sql += " ORDER BY rank LIMIT ?"
    params.append(max(1, min(int(limit), 20)))
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        return _cold()
    evidence: List[dict] = [_meta(r, r["excerpt"]) for r in rows]
    top = rows[0]
    def _fwd_line(m: dict) -> str:
        if not m.get("forwarded"):
            return ""
        who = f"; its From header reads: {m['forwarded_from']}" if m.get("forwarded_from") else ""
        return (f"\nForwarded: the excerpt contains or follows a forwarded or quoted message "
                f"marker ({m['forward_marker']}); words after that marker were not written by "
                f"the email's sender{who}")

    content = "\n\n".join(
        f"{m['evidence_id']}\nFile: {m['source_path']}\nFrom: {m['sender']}\n"
        f"Date: {m['date']}\nSubject: {m['subject']}\nExcerpt: {m['excerpt']}{_fwd_line(m)}"
        for m in evidence)
    return {
        "source": top["evidence_id"],
        "content": content,
        "summary": (f"Found {len(rows)} email(s). Top match: from {top['sender']} "
                    f"on {top['date_iso']}, subject \"{top['subject']}\"."),
        "evidence": evidence,
    }


def read_email(db_path: str, evidence_id: str) -> dict:
    with _connect(db_path) as conn:
        r = conn.execute("SELECT * FROM emails WHERE evidence_id = ?", (evidence_id,)).fetchone()
    if r is None:
        return _cold()
    with _connect(db_path) as conn:
        also = [x["source_path"] for x in conn.execute(
            "SELECT source_path FROM duplicate_paths WHERE evidence_id = ? ORDER BY source_path",
            (r["evidence_id"],))]
    content = (f"From: {r['sender']}\nTo: {r['recipients']}\nDate: {r['date_iso']}\n"
               f"Subject: {r['subject']}\nMessage-ID: {r['message_id']}\n"
               f"File: {r['source_path']}\n\n{r['body']}")
    meta = _meta(r)
    meta["also_filed_at"] = also
    return {
        "source": r["evidence_id"],
        "content": content,
        "summary": f"Email from {r['sender']} on {r['date_iso']}, subject \"{r['subject']}\".",
        "evidence": [meta],
    }


def who_wrote_to(db_path: str, person: str, date_from: Optional[str] = None,
                 date_to: Optional[str] = None, limit: int = 10) -> dict:
    """Emails sent by or to anyone whose address/name contains `person`, oldest first."""
    date_from, date_to = _check_date(date_from, "date_from"), _check_date(date_to, "date_to")
    person = (person or "").strip()
    if not person:
        return _cold()
    like = _like(person)
    params: list = [like, like]
    sql = ("SELECT * FROM emails WHERE (sender LIKE ? ESCAPE '\\' OR recipients LIKE ? ESCAPE '\\')")
    sql += _date_clauses(date_from, date_to, "date_iso", params)
    sql += " ORDER BY date_iso LIMIT ?"
    params.append(max(1, min(int(limit), 50)))
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    if not rows:
        return _cold()
    evidence = [_meta(r) for r in rows]
    content = "\n".join(
        f"{m['date']} | {m['sender']} -> {m['recipients']} | {m['subject']} | "
        f"{m['evidence_id']} | {m['source_path']}" for m in evidence)
    return {
        "source": rows[0]["evidence_id"],
        "content": content,
        "summary": (f"Found {len(rows)} email(s) involving {person}; earliest from "
                    f"{rows[0]['sender']} on {rows[0]['date_iso']}."),
        "evidence": evidence,
    }


_ADDRESS = re.compile(r"[\w.'+-]+@[\w.-]+")


def find_person(db_path: str, name: str, limit: int = 8) -> dict:
    """Look up the real email addresses in the archive that match a person's name.

    Every word of `name` must appear in the address ("Ken Lay" matches kenneth.lay@enron.com).
    This is an address lookup, not an email: its `source` is "address-lookup:<name>", it
    has no `evidence` of the email kind, and it exists so the model never has to guess an
    address before filtering by sender or recipient.
    """
    words = [w.lower() for w in _TOKEN.findall(name or "") if len(w) > 1 or w.isalnum()]
    if not words:
        return _cold()
    like_args = [_like(w) for w in words]
    cond = " AND ".join(["{c} LIKE ? ESCAPE '\\'"] * len(words))
    sent: dict = {}
    got: dict = {}
    with _connect(db_path) as conn:
        for r in conn.execute(
                "SELECT lower(sender) AS a, count(*) AS n FROM emails WHERE "
                + cond.format(c="sender") + " GROUP BY lower(sender) ORDER BY n DESC LIMIT 50",
                like_args):
            sent[r["a"]] = r["n"]
        for r in conn.execute(
                "SELECT recipients FROM emails WHERE " + cond.format(c="recipients"),
                like_args):
            for a in set(_ADDRESS.findall(r["recipients"].lower())):
                if all(w in a for w in words):
                    got[a] = got.get(a, 0) + 1
    addresses = [a for a in set(sent) | set(got) if all(w in a for w in words)]
    if not addresses:
        return _cold()
    addresses.sort(key=lambda a: (-(sent.get(a, 0) + got.get(a, 0)), a))
    addresses = addresses[:max(1, min(int(limit), 20))]
    evidence = [{"address": a, "as_sender": sent.get(a, 0), "as_recipient": got.get(a, 0)}
                for a in addresses]
    content = "\n".join(
        f"{e['address']} | sender in {e['as_sender']} email(s) | recipient in {e['as_recipient']} email(s)"
        for e in evidence)
    return {
        "source": f"address-lookup:{' '.join(words)}",
        "content": content,
        "summary": f"Found {len(evidence)} address(es) matching {name!r}; most frequent: {addresses[0]}.",
        "evidence": evidence,
    }
