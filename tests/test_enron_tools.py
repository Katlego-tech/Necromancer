"""Enron tool tests against tests/fixtures/maildir (synthetic emails, not real Enron data)."""
import os
import tempfile
import unittest

from spirits.enron import ingest, tools

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "maildir")


class EnronToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "t.sqlite")
        ingest.build_index(FIXTURES, cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_search_returns_traceable_evidence(self):
        r = tools.search_emails(self.db, "widget losing money")
        self.assertEqual(r["source"], "<fx1@test>")
        e = r["evidence"][0]
        self.assertEqual(e["evidence_id"], "<fx1@test>")
        self.assertEqual(e["source_path"], "allen/all_documents/3.")
        self.assertEqual(e["sender"], "alice.synthetic@example.test")
        self.assertEqual(e["date"], "2001-05-14T23:39:00")
        self.assertIn("losing money", e["excerpt"])
        self.assertIn("allen/all_documents/3.", r["content"])

    def test_search_returns_a_duplicated_message_once(self):
        r = tools.search_emails(self.db, "widget losing money")
        self.assertEqual([e["evidence_id"] for e in r["evidence"]], ["<fx1@test>"])

    def test_search_stems_words(self):
        self.assertEqual(tools.search_emails(self.db, "loses money")["source"], "<fx1@test>")

    def test_search_no_results_goes_cold(self):
        self.assertEqual(tools.search_emails(self.db, "zeppelin"),
                         {"source": None, "content": None, "summary": tools.COLD, "evidence": []})

    def test_search_survives_fts_syntax_characters(self):
        for q in ('widget" OR (NEAR', "*", "AND", "'; DROP TABLE emails; --"):
            self.assertIn("summary", tools.search_emails(self.db, q))

    def test_search_date_range(self):
        r = tools.search_emails(self.db, "widget", date_from="2001-09-01", date_to="2001-12-31")
        self.assertEqual([e["evidence_id"] for e in r["evidence"]], ["<fx2@test>"])

    def test_search_date_to_is_inclusive_of_whole_day(self):
        r = tools.search_emails(self.db, "widget", date_from="2001-05-14", date_to="2001-05-14")
        self.assertEqual(r["source"], "<fx1@test>")

    def test_search_sender_filter(self):
        r = tools.search_emails(self.db, "widget", sender="bob.synthetic")
        self.assertEqual([e["evidence_id"] for e in r["evidence"]], ["<fx2@test>"])
        self.assertEqual(tools.search_emails(self.db, "widget", sender="nobody")["source"], None)

    def test_search_recipient_filter_covers_to_and_cc(self):
        r = tools.search_emails(self.db, "widget", recipient="dave.synthetic")
        self.assertEqual([e["evidence_id"] for e in r["evidence"]], ["<fx2@test>"])
        self.assertIsNone(tools.search_emails(self.db, "widget", recipient="%")["source"])

    def test_sender_filter_treats_wildcards_literally(self):
        self.assertIsNone(tools.search_emails(self.db, "widget", sender="%")["source"])

    def test_bad_date_format_raises(self):
        with self.assertRaises(ValueError):
            tools.search_emails(self.db, "widget", date_from="May 2001")

    def test_emails_with_unparseable_date_excluded_from_date_range(self):
        r = tools.search_emails(self.db, "broken", date_from="1990-01-01", date_to="2030-01-01")
        self.assertIsNone(r["source"])
        self.assertEqual(tools.search_emails(self.db, "broken")["source"], "<fx5@test>")

    def test_read_email_verbatim_body_and_path(self):
        r = tools.read_email(self.db, "<fx1@test>")
        self.assertIn("The widget partnership is losing money this quarter.", r["content"])
        self.assertIn("allen/all_documents/3.", r["content"])

    def test_read_email_lists_other_paths_it_was_filed_under(self):
        e = tools.read_email(self.db, "<fx1@test>")["evidence"][0]
        self.assertEqual(e["also_filed_at"], ["allen/discussion_threads/10.", "allen/inbox/1."])

    def test_read_email_by_path_fallback_id(self):
        r = tools.read_email(self.db, "path:lay/inbox/4.")
        self.assertEqual(r["evidence"][0]["source_path"], "lay/inbox/4.")

    def test_read_unknown_email_goes_cold(self):
        self.assertEqual(tools.read_email(self.db, "<nope>")["summary"], tools.COLD)

    def test_who_wrote_to_matches_sender_recipient_and_cc(self):
        r = tools.who_wrote_to(self.db, "dave.synthetic")
        self.assertEqual([e["evidence_id"] for e in r["evidence"]], ["<fx2@test>"])
        r = tools.who_wrote_to(self.db, "alice.synthetic@example.test")
        self.assertEqual({e["evidence_id"] for e in r["evidence"]}, {"<fx1@test>", "<fx2@test>"})

    def test_who_wrote_to_unknown_goes_cold(self):
        self.assertEqual(tools.who_wrote_to(self.db, "nobody")["summary"], tools.COLD)


if __name__ == "__main__":
    unittest.main()
