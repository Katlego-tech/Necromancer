"""Forwarded-quote detection. All emails are SYNTHETIC test fixtures, not real Enron content."""
import os
import tempfile
import unittest

from spirits.enron import ingest, tools

FWD = (
    "Message-ID: <w1@test>\nDate: Wed, 24 Oct 2001 17:09:03 -0700\n"
    "From: carol.synthetic@example.test\nTo: dan.synthetic@example.test\n"
    "Subject: FW: ledger concerns\n\n"
    "Dan per my voice mail, see below.\r\n\r\n"
    "-----Original Message-----\r\n"
    "From: Erin Synthetic\r\nSent: Wednesday, October 24, 2001 4:14 PM\r\n"
    "To: Carol Synthetic\r\nSubject: ledger concerns\r\n\r\n"
    "I believe there is more bad news coming about the zebra ledger deals.\r\n"
)
FWD_BY = (
    "Message-ID: <w2@test>\nDate: Thu, 25 Oct 2001 09:00:00 -0700\n"
    "From: frank.synthetic@example.test\nTo: dan.synthetic@example.test\n"
    "Subject: Fw: ledger notes\n\n"
    "Please read.\n\n"
    "---------------------- Forwarded by Frank Synthetic/HOU/ECT on 10/25/2001 09:00 AM ---------------------------\n"
    "The quokka reserve figures were restated twice this quarter.\n"
)
PLAIN = (
    "Message-ID: <w3@test>\nDate: Fri, 26 Oct 2001 09:00:00 -0700\n"
    "From: gina.synthetic@example.test\nTo: dan.synthetic@example.test\n"
    "Subject: ledger plain\n\n"
    "The wombat ledger was approved today by the committee.\n"
)
OWN_THEN_FWD = (
    "Message-ID: <w4@test>\nDate: Sat, 27 Oct 2001 09:00:00 -0700\n"
    "From: hank.synthetic@example.test\nTo: dan.synthetic@example.test\n"
    "Subject: Re: ledger\n\n"
    "The platypus ledger needs a second review before Friday. Please bring the quarterly summary, the "
    "reserve schedule, the counterparty list, the open questions from the last meeting and the draft "
    "memo so that the committee can finish the work on time and without any further delay at all.\n\n"
    "-----Original Message-----\nFrom: Ivy Synthetic\nSubject: ledger\n\nthanks for the update\n"
)


class ForwardedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = os.path.join(cls.tmp.name, "maildir", "u", "inbox")
        os.makedirs(root)
        for n, text in enumerate((FWD, FWD_BY, PLAIN, OWN_THEN_FWD), 1):
            with open(os.path.join(root, f"{n}."), "w", newline="") as f:
                f.write(text)
        cls.db = os.path.join(cls.tmp.name, "t.sqlite")
        ingest.build_index(os.path.join(cls.tmp.name, "maildir"), cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ev(self, query):
        r = tools.search_emails(self.db, query)
        return r, r["evidence"][0]

    def test_quote_below_original_message_marker_is_flagged_with_original_author(self):
        r, e = self.ev("zebra ledger deals")
        self.assertTrue(e["forwarded"])
        self.assertEqual(e["forward_marker"], "-----Original Message-----")
        self.assertEqual(e["forwarded_from"], "Erin Synthetic")
        self.assertIn("Forwarded: the excerpt contains or follows a forwarded or quoted message", r["content"])
        self.assertIn("words after that marker were not written by the email's sender", r["content"])
        self.assertIn("its From header reads: Erin Synthetic", r["content"])

    def test_forwarded_by_marker_is_flagged_without_inventing_an_author(self):
        r, e = self.ev("quokka reserve figures")
        self.assertTrue(e["forwarded"])
        self.assertTrue(e["forward_marker"].startswith("---------------------- Forwarded by Frank Synthetic"))
        self.assertNotIn("forwarded_from", e)
        self.assertNotIn("its From header reads", r["content"])

    def test_email_without_a_marker_is_not_flagged(self):
        r, e = self.ev("wombat ledger approved")
        self.assertNotIn("forwarded", e)
        self.assertNotIn("Forwarded:", r["content"])

    def test_senders_own_text_above_the_marker_is_not_flagged(self):
        r, e = self.ev("platypus ledger second review")
        self.assertNotIn("Original Message", e["excerpt"])
        self.assertNotIn("forwarded", e)

    def test_excerpt_stays_verbatim_and_metadata_values_are_clean_scalars(self):
        _, e = self.ev("zebra ledger deals")
        self.assertNotIn("\r", e["forwarded_from"])
        for v in e.values():
            self.assertIsInstance(v, (str, int, float, bool, type(None)))

    def test_excerpt_spanning_sender_text_and_marker_is_flagged_and_keeps_the_marker_visible(self):
        _, e = self.ev("quokka reserve figures")
        self.assertIn("Please read.", e["excerpt"])
        self.assertIn(e["forward_marker"], e["excerpt"])

    def test_persona_forbids_attributing_forwarded_words_to_the_sender(self):
        from spirits.enron import spirit
        self.assertIn("forwarded or quoted message marker", spirit.PERSONA)
        self.assertIn("Never attribute forwarded words to the sender", spirit.PERSONA)


if __name__ == "__main__":
    unittest.main()
