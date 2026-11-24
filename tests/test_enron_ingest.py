"""Ingestion tests against tests/fixtures/maildir (synthetic emails, not real Enron data)."""
import os
import shutil
import sqlite3
import tempfile
import unittest

from spirits.enron import ingest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "maildir")


class IngestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.maildir = os.path.join(cls.tmp.name, "maildir")
        shutil.copytree(FIXTURES, cls.maildir)
        open(os.path.join(cls.maildir, "lay", "inbox", "6."), "wb").close()  # empty file
        cls.db = os.path.join(cls.tmp.name, "t.sqlite")
        cls.stats = ingest.build_index(cls.maildir, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def row(self, evidence_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute("SELECT * FROM emails WHERE evidence_id=?", (evidence_id,)).fetchone()
        finally:
            conn.close()

    def test_counts(self):
        s = self.stats
        self.assertEqual(
            (s.files_seen, s.indexed, s.duplicates, len(s.failures), s.missing_date, s.missing_message_id),
            (12, 8, 2, 2, 1, 1))

    def test_failures_are_recorded_with_reason_and_do_not_stop_the_run(self):
        failed = dict(self.stats.failures)
        self.assertEqual(failed["lay/inbox/6."], "empty file")
        self.assertIn("no From/Subject/Date headers", failed["lay/inbox/7."])

    def test_duplicate_message_id_keeps_first_seen_path(self):
        self.assertEqual(self.row("<fx1@test>")["source_path"], "allen/all_documents/3.")

    def test_same_content_under_different_message_id_is_deduplicated(self):
        self.assertIsNone(self.row("<fx1b@test>"))
        conn = sqlite3.connect(self.db)
        rows = conn.execute("SELECT source_path, evidence_id, reason FROM duplicate_paths "
                            "ORDER BY source_path").fetchall()
        conn.close()
        self.assertEqual(rows, [
            ("allen/discussion_threads/10.", "<fx1@test>", "content"),
            ("allen/inbox/1.", "<fx1@test>", "message-id"),
        ])

    def test_fields_preserved(self):
        r = self.row("<fx2@test>")
        self.assertEqual(r["sender"], "bob.synthetic@example.test")
        self.assertIn("carol.synthetic@example.test", r["recipients"])
        self.assertIn("dave.synthetic@example.test", r["recipients"])  # Cc kept
        self.assertEqual(r["subject"], "Re: Widget losses")
        self.assertEqual(r["date_iso"], "2001-10-02T14:00:00")  # normalised to UTC
        self.assertIn("gadget numbers", r["body"])
        self.assertEqual(r["source_path"], "allen/sent/2.")

    def test_missing_message_id_falls_back_to_path_evidence_id(self):
        r = self.row("path:lay/inbox/4.")
        self.assertIsNone(r["message_id"])
        self.assertIn("falls back to the file path", r["body"])

    def test_bad_date_is_indexed_with_null_date(self):
        r = self.row("<fx5@test>")
        self.assertIsNone(r["date_iso"])

    def test_unknown_charset_still_yields_body(self):
        self.assertIn("does not exist", self.row("<fx8@test>")["body"])

    def test_multipart_uses_plain_part(self):
        body = self.row("<fx9@test>")["body"]
        self.assertIn("raptor ledger", body)
        self.assertNotIn("<p>", body)

    def test_address_list_rejected_by_strict_parser_is_still_indexed(self):
        r = self.row("<fx11@test>")
        self.assertIn("frank.synthetic@example.test", r["recipients"])
        self.assertIn(".dotted@example.test", r["recipients"])

    def test_quoted_printable_body_is_stored_decoded(self):
        body = self.row("<fx12@test>")["body"]
        self.assertIn("coming re: these synthetic ledger deals.", body)
        self.assertNotIn("=\n", body)

    def test_retry_only_processes_listed_paths(self):
        db = os.path.join(self.tmp.name, "retry.sqlite")
        s = ingest.build_index(self.maildir, db, only=["lay/inbox/11.", "lay/inbox/7."])
        self.assertEqual((s.files_seen, s.indexed, len(s.failures)), (2, 1, 1))

    def test_limit_caps_indexed_count_and_rerun_is_idempotent(self):
        db = os.path.join(self.tmp.name, "limit.sqlite")
        self.assertEqual(ingest.build_index(self.maildir, db, limit=2).indexed, 2)
        again = ingest.build_index(self.maildir, db)
        self.assertEqual(again.indexed, 6)       # remaining 6 new
        self.assertEqual(again.duplicates, 4)    # 2 already indexed + 2 known duplicates
        conn = sqlite3.connect(db)
        self.assertEqual(conn.execute("SELECT count(*) FROM duplicate_paths").fetchone()[0], 2)
        conn.close()
        conn = sqlite3.connect(db)
        self.assertEqual(conn.execute("SELECT count(*) FROM emails").fetchone()[0], 8)
        self.assertEqual(conn.execute("SELECT count(*) FROM emails_fts").fetchone()[0], 8)
        conn.close()


if __name__ == "__main__":
    unittest.main()
