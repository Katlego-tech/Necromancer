"""Grounding-rule tests (see .kiro/steering/grounding.md). Fixtures are synthetic."""
import os
import re
import sqlite3
import tempfile
import unittest

from spirits.enron import ingest, tools

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "maildir")
COLD = {"source": None, "content": None, "summary": "The trail goes cold.", "evidence": []}


class GroundingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "g.sqlite")
        ingest.build_index(FIXTURES, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_tool_returns_contract_keys(self):
        for r in (tools.search_emails(self.db, "widget"),
                  tools.read_email(self.db, "<fx1@test>"),
                  tools.who_wrote_to(self.db, "alice"),
                  tools.search_emails(self.db, "zzzz")):
            self.assertEqual(set(r), {"source", "content", "summary", "evidence"})

    def test_empty_result_is_exactly_the_cold_trail(self):
        for r in (tools.search_emails(self.db, "zzzz"),
                  tools.read_email(self.db, "<missing>"),
                  tools.who_wrote_to(self.db, "zzzz")):
            self.assertEqual(r, COLD)

    def test_search_excerpt_is_verbatim_from_stored_body(self):
        r = tools.search_emails(self.db, "gadget board")
        conn = sqlite3.connect(self.db)
        try:
            for e in r["evidence"]:
                body = conn.execute("SELECT body FROM emails WHERE evidence_id=?",
                                    (e["evidence_id"],)).fetchone()[0]
                for piece in e["excerpt"].split(" ... "):
                    self.assertIn(piece.strip(), body)
        finally:
            conn.close()

    def test_summary_facts_come_from_the_record(self):
        r = tools.search_emails(self.db, "widget")
        e = r["evidence"][0]
        self.assertIn(e["sender"], r["summary"])
        self.assertIn(e["date"], r["summary"])
        self.assertIn(e["subject"], r["summary"])

    def test_every_evidence_entry_traces_to_a_row_and_a_file_path(self):
        r = tools.search_emails(self.db, "synthetic")
        for e in r["evidence"]:
            self.assertTrue(e["source_path"])
            self.assertEqual(tools.read_email(self.db, e["evidence_id"])["source"], e["evidence_id"])
            self.assertTrue(os.path.isfile(os.path.join(FIXTURES, e["source_path"])))


if __name__ == "__main__":
    unittest.main()
