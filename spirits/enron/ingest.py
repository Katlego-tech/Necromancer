"""Build a SQLite FTS5 index from an extracted CMU Enron maildir tree.

Each email gets an `evidence_id`: its Message-ID when present, otherwise
`path:<relative source path>`. The relative `source_path` (e.g. `allen-p/_sent_mail/1.`)
is stored for every row so any citation traces back to a file on disk.

Usage:
    python3 -m spirits.enron.ingest MAILDIR DB [--limit N] [--failure-log FILE]
"""
from __future__ import annotations

import argparse
import email
import email.policy
import email.utils
import hashlib
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import timezone
from typing import Iterator, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id          INTEGER PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    message_id  TEXT,
    source_path TEXT NOT NULL,
    sender      TEXT,
    recipients  TEXT,
    date_iso    TEXT,
    subject     TEXT,
    body        TEXT,
    content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_emails_hash ON emails(content_hash);
CREATE TABLE IF NOT EXISTS duplicate_paths (
    source_path TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL,
    reason      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_iso);
CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender);
CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
    subject, body, sender, recipients,
    content='emails', content_rowid='id',
    tokenize='porter unicode61'
);
"""


class MalformedEmail(Exception):
    pass


@dataclass
class IngestStats:
    files_seen: int = 0
    indexed: int = 0
    duplicates: int = 0
    missing_date: int = 0
    missing_message_id: int = 0
    failures: List[Tuple[str, str]] = field(default_factory=list)


def _clean(text: object) -> str:
    return str(text or "").replace("\x00", "").strip()


def parse_email_file(path: str, rel_path: str) -> dict:
    """Parse one maildir file. Raises MalformedEmail if nothing usable is found."""
    with open(path, "rb") as f:
        raw = f.read()
    if not raw.strip():
        raise MalformedEmail("empty file")
    msg = email.message_from_bytes(raw, policy=email.policy.default)

    legacy = []  # lazily-built compat32 parse, for headers the strict parser rejects

    def header(name: str) -> str:
        try:
            return _clean(msg.get(name))
        except Exception:  # e.g. stdlib TypeError on "a@x, .b@x" address lists
            if not legacy:
                legacy.append(email.message_from_bytes(raw))
            return " ".join(_clean(legacy[0].get(name)).split())

    sender, subject, raw_date = header("From"), header("Subject"), header("Date")
    if not (sender or subject or raw_date):
        raise MalformedEmail("no From/Subject/Date headers")

    date_iso = None
    if raw_date:
        try:
            dt = email.utils.parsedate_to_datetime(raw_date)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)
            date_iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
        except (TypeError, ValueError, IndexError, OverflowError):
            date_iso = None

    recipients = ", ".join(v for v in (header("To"), header("Cc")) if v)

    body = ""
    try:
        part = msg.get_body(preferencelist=("plain",))
        if part is not None:
            body = part.get_content()
        elif not msg.is_multipart():
            body = msg.get_content()
    except Exception:  # unknown charset, bad transfer encoding
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            body = payload.decode("utf-8", errors="replace")
    body = _clean(body) if isinstance(body, str) else ""

    message_id = header("Message-ID")
    fingerprint = "\x1f".join((sender, date_iso or "", subject, " ".join(body.split())))
    return {
        "content_hash": hashlib.sha1(fingerprint.encode("utf-8", "replace")).hexdigest(),
        "evidence_id": message_id or f"path:{rel_path}",
        "message_id": message_id or None,
        "source_path": rel_path,
        "sender": sender,
        "recipients": recipients,
        "date_iso": date_iso,
        "subject": subject,
        "body": body,
    }


def iter_maildir_files(root: str) -> Iterator[str]:
    """Deterministic walk; does not follow symlinks."""
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(dirpath, name)
            if os.path.isfile(path) and not os.path.islink(path):
                yield path


def build_index(maildir: str, db_path: str, limit: Optional[int] = None,
                progress_every: int = 0, only: Optional[List[str]] = None) -> IngestStats:
    """Index every parseable email under maildir. `limit` caps files *indexed*.
    `only` restricts the run to those maildir-relative paths (used to retry failures)."""
    stats = IngestStats()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA synchronous = OFF")
    root = os.path.abspath(maildir)
    paths = ([os.path.join(root, r) for r in only] if only is not None
             else iter_maildir_files(root))
    for path in paths:
        if limit is not None and stats.indexed >= limit:
            break
        stats.files_seen += 1
        rel = os.path.relpath(path, root)
        try:
            rec = parse_email_file(path, rel)
        except MalformedEmail as e:
            stats.failures.append((rel, str(e)))
            continue
        except Exception as e:  # never let one bad file stop the run
            stats.failures.append((rel, f"{type(e).__name__}: {e}"))
            continue
        # Duplicates: same Message-ID, or same sender/date/subject/body filed under a
        # different Message-ID (Enron files one message in many folders). First seen wins;
        # the other paths are kept in duplicate_paths so they stay traceable.
        existing = conn.execute(
            "SELECT evidence_id, source_path, 'message-id' FROM emails WHERE evidence_id = ? "
            "UNION ALL SELECT evidence_id, source_path, 'content' FROM emails "
            "WHERE content_hash = ? LIMIT 1", (rec["evidence_id"], rec["content_hash"])).fetchone()
        if existing:
            stats.duplicates += 1
            if existing[1] != rel:  # not merely a re-run over the same file
                conn.execute("INSERT OR IGNORE INTO duplicate_paths VALUES (?, ?, ?)",
                             (rel, existing[0], existing[2]))
            continue
        cur = conn.execute(
            "INSERT INTO emails (evidence_id, message_id, source_path, sender, "
            "recipients, date_iso, subject, body, content_hash) VALUES (:evidence_id, "
            ":message_id, :source_path, :sender, :recipients, :date_iso, :subject, :body, "
            ":content_hash)", rec)
        conn.execute(
            "INSERT INTO emails_fts(rowid, subject, body, sender, recipients) "
            "VALUES (?, ?, ?, ?, ?)",
            (cur.lastrowid, rec["subject"], rec["body"], rec["sender"], rec["recipients"]))
        stats.indexed += 1
        stats.missing_date += rec["date_iso"] is None
        stats.missing_message_id += rec["message_id"] is None
        if stats.indexed % 5000 == 0:
            conn.commit()
        if progress_every and stats.files_seen % progress_every == 0:
            print(f"[ingest] seen={stats.files_seen} indexed={stats.indexed} "
                  f"dup={stats.duplicates} fail={len(stats.failures)}", file=sys.stderr, flush=True)
    conn.commit()
    conn.close()
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("maildir")
    ap.add_argument("db")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--failure-log", default=None)
    ap.add_argument("--retry", default=None, metavar="FAILURE_LOG",
                    help="only re-ingest the paths listed in a previous failure log")
    a = ap.parse_args(argv)
    only = None
    if a.retry:
        with open(a.retry) as f:
            only = [line.split("\t")[0] for line in f if line.strip()]
    s = build_index(a.maildir, a.db, a.limit, progress_every=10000, only=only)
    if a.failure_log:
        with open(a.failure_log, "w") as f:
            for rel, why in s.failures:
                f.write(f"{rel}\t{why}\n")
    print(f"files_seen={s.files_seen} indexed={s.indexed} duplicates={s.duplicates} "
          f"failures={len(s.failures)} missing_date={s.missing_date} "
          f"missing_message_id={s.missing_message_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
