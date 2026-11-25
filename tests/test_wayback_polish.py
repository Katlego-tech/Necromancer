"""Spoken dates, thin pages, the offline natural path and the date-aware grounding check.
All pages, listings and dates are SYNTHETIC; the network is never touched."""
import json
import os
import sys
import tempfile
import unittest

from spirits.wayback import spirit, tools
from tests.test_wayback_tools import Fake
from voice.grounding_check import check_turn, date_mentions, digit_runs, quoted_spans

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import prewarm_wayback  # noqa: E402

URL = "http://www.example.test:80/"
PAGE = (b"<html><title>Synthetic Page</title><body><h1>Synthetic headline</h1>"
        b"<p>Tortoises are breeding this spring season.</p><p>Another synthetic sentence of text here.</p>"
        b"<p>A third synthetic line with enough words in it.</p>"
        b"<p>Padding so that the page is clearly longer than the sparse limit of two hundred "
        b"characters, which is a synthetic number chosen only for this test.</p></body></html>")
CDX = [["timestamp", "original", "mimetype", "statuscode"],
       ["19990508060549", URL, "text/html", "200"],
       ["19991010024340", URL, "text/html", "200"]]


def served(ts, body=PAGE):
    return (200, f"https://web.archive.org/web/{ts}id_/{URL}", body)


class RoutedFetch:
    """CDX for listing URLs, a page for snapshot URLs; counts calls."""
    def __init__(self, bodies=None):
        self.calls, self.bodies = [], bodies or {}

    def __call__(self, url):
        self.calls.append(url)
        if "/cdx/" in url:
            return 200, url, json.dumps(CDX).encode()
        ts = url.split("/web/")[1][:14]
        return served(ts, self.bodies.get(ts, PAGE))


def no_net(url):
    raise AssertionError("network must not be used: " + url)


class SpokenDateTest(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(tools.spoken_date("20000617112828"), "June 17, 2000")
        self.assertEqual(tools.spoken_date("19990101000000"), "January 1, 1999")
        self.assertEqual(tools.spoken_date("20041231235959"), "December 31, 2004")

    def test_leap_day_and_calendar_edges(self):
        self.assertEqual(tools.spoken_date("20000229000000"), "February 29, 2000")
        self.assertIsNone(tools.spoken_date("19990229000000"))  # 1999 was not a leap year
        self.assertIsNone(tools.spoken_date("19990431000000"))
        self.assertIsNone(tools.spoken_date("19991301000000"))
        self.assertIsNone(tools.spoken_date("19990001000000"))
        self.assertIsNone(tools.spoken_date("00000101000000"))

    def test_bad_input(self):
        for bad in (None, 20000617112828, "", "2000", "2000061711282", "200006171128288",
                    "2000-06-17 11:28", "2000061711282x", b"20000617112828", ["20000617112828"]):
            with self.subTest(bad=bad):
                self.assertIsNone(tools.spoken_date(bad))

    def test_spoken_site(self):
        self.assertEqual(tools.spoken_site("http://www.pets.com:80/"), "pets.com")
        self.assertEqual(tools.spoken_site("http://pets.com/"), "pets.com")
        self.assertEqual(tools.spoken_site("http://www/"), "www")
        self.assertIsNone(tools.spoken_site("nonsense"))
        self.assertIsNone(tools.spoken_site(None))

    def test_tool_results_carry_the_spoken_form_and_keep_the_digits(self):
        with tempfile.TemporaryDirectory() as d:
            f = RoutedFetch()
            lst = tools.list_snapshots(URL, 1999, cache_dir=d, fetch=f, sleep=lambda s: None)
            self.assertEqual([e["spoken_date"] for e in lst["evidence"]],
                             ["May 8, 1999", "October 10, 1999"])
            self.assertIn("May 8, 1999", lst["summary"])
            self.assertIn("19990508060549", lst["content"])
            r = tools.read_snapshot(URL, "19990508060549", cache_dir=d, fetch=f, sleep=lambda s: None)
            self.assertEqual((r["spoken_date"], r["spoken_site"]), ("May 8, 1999", "example.test"))
            self.assertIn("19990508060549", r["summary"])
            ev = spirit.make_tools(d, f)[1].to_evidence(r)
            self.assertEqual(ev["metadata"], {"url": URL, "timestamp": "19990508060549"})

    def test_impossible_date_still_reports_the_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            r = tools.read_snapshot(URL, "19990431000000", cache_dir=d,
                                    fetch=Fake(served("19990431000000")), sleep=lambda s: None)
            self.assertIsNone(r["spoken_date"])
            self.assertIn("19990431000000", r["summary"])


class ThinPageTest(unittest.TestCase):
    def read(self, body):
        with tempfile.TemporaryDirectory() as d:
            return tools.read_snapshot(URL, "19990508060549", cache_dir=d,
                                       fetch=Fake(served("19990508060549", body)), sleep=lambda s: None)

    def test_short_text_is_marked_sparse(self):
        r = self.read(b"<html><body><p>Under construction.</p></body></html>")
        self.assertTrue(r["evidence"][0]["sparse"])
        self.assertIn("sparse", r["summary"])

    def test_menu_page_is_marked_label_heavy(self):
        items = "".join(f"<li>Item {i}</li>" for i in range(30))
        body = ("<html><body><ul>%s</ul><p>One longer sentence appears in this page text.</p>"
                "<p>Another longer sentence appears in this page text too.</p>"
                "<p>A third longer sentence appears in this page text as well.</p></body></html>" % items)
        r = self.read(body.encode())
        self.assertTrue(r["evidence"][0].get("label_heavy"))
        self.assertNotIn("sparse", r["evidence"][0])
        self.assertIn("short labels", r["summary"])

    def test_readable_page_has_no_flag(self):
        e = self.read(PAGE)["evidence"][0]
        self.assertNotIn("sparse", e)
        self.assertNotIn("label_heavy", e)


class OfflineNaturalPathTest(unittest.TestCase):
    """"Summon <site> in 1999. What were you selling?" is list_snapshots(url, year), then
    read_snapshot with the URL the listing gave. After the pre-warm neither touches the network."""

    def test_prewarm_then_list_then_read_without_network_and_prewarm_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            f = RoutedFetch()
            prewarm_wayback.DEMO_YEARS, saved = [(URL, "1999")], prewarm_wayback.DEMO_YEARS
            try:
                first = prewarm_wayback.demo(d, pause=lambda s: None, fetch=f)
                n = len(f.calls)
                second = prewarm_wayback.demo(d, pause=lambda s: None, fetch=no_net)
            finally:
                prewarm_wayback.DEMO_YEARS = saved
            self.assertEqual(first, 3)   # one listing and two snapshots
            self.assertEqual(n, 3)
            self.assertEqual(second, 0)  # nothing left to fetch
            t = {x.name: x for x in spirit.make_tools(d, no_net, lambda s: None)}
            lst = t["list_snapshots"].handler({"url": URL, "year": "1999"})
            self.assertEqual(len(lst["evidence"]), 2)
            first_row = lst["evidence"][0]
            r = t["read_snapshot"].handler({"url": first_row["original"], "timestamp": first_row["timestamp"]})
            self.assertIn("Tortoises are breeding this spring season.", r["content"])
            self.assertEqual(r["spoken_date"], "May 8, 1999")

    def test_spoken_site_name_and_host_variants_hit_the_same_cache(self):
        with tempfile.TemporaryDirectory() as d:
            f = RoutedFetch()
            tools.list_snapshots("http://www.example.test/", 1999, cache_dir=d, fetch=f, sleep=lambda s: None)
            tools.read_snapshot(URL, "19990508060549", cache_dir=d, fetch=f, sleep=lambda s: None)
            calls = len(f.calls)
            for spoken in ("example.test", "www.example.test", "https://example.test/", URL):
                r = tools.list_snapshots(spoken, "1999", cache_dir=d, fetch=no_net, sleep=lambda s: None)
                self.assertEqual(len(r["evidence"]), 2, spoken)
            for variant in ("http://www.example.test/", "http://example.test/", "example.test"):
                r = tools.read_snapshot(variant, "19990508060549", cache_dir=d, fetch=no_net,
                                        sleep=lambda s: None)
                self.assertEqual(r["evidence"][0]["timestamp"], "19990508060549", variant)
            self.assertEqual(len(f.calls), calls)

    def test_scheme_less_nonsense_is_still_rejected(self):
        for bad in ("pets", "not a url", "ftp://pets.com/", "javascript:alert(1)"):
            self.assertEqual(tools.list_snapshots(bad, 1999, fetch=no_net)["summary"], tools.COLD, bad)

    def test_pick_reads_spreads_evenly_and_keeps_short_lists(self):
        ts = [f"1999{m:02d}01000000" for m in range(1, 13)]
        self.assertEqual(prewarm_wayback.pick_reads(ts[:3]), ts[:3])
        picked = prewarm_wayback.pick_reads(ts, 4)
        self.assertEqual((picked[0], picked[-1], len(picked)), (ts[0], ts[-1], 4))

    def test_capture_with_no_text_is_cold_and_not_fetched_again(self):
        with tempfile.TemporaryDirectory() as d:
            refresh = b'<html><head><meta http-equiv="refresh" content="1;URL=/x"></head><body></body></html>'
            f = Fake(served("19991010024340", refresh))
            first = tools.read_snapshot(URL, "19991010024340", cache_dir=d, fetch=f, sleep=lambda s: None)
            second = tools.read_snapshot(URL, "19991010024340", cache_dir=d, fetch=no_net, sleep=lambda s: None)
            self.assertEqual((first["summary"], second["summary"]), (tools.COLD, tools.COLD))
            self.assertEqual(len(f.calls), 1)

    def test_year_with_no_captures_is_the_cold_trail_not_a_guess(self):
        with tempfile.TemporaryDirectory() as d:
            f = Fake((200, "x", json.dumps(CDX[:1]).encode()))
            r = tools.list_snapshots(URL, 2003, cache_dir=d, fetch=f, sleep=lambda s: None)
            self.assertEqual(r["summary"], tools.COLD)


class GroundingDateTest(unittest.TestCase):
    def results(self):
        with tempfile.TemporaryDirectory() as d:
            f = RoutedFetch()
            lst = tools.list_snapshots(URL, 1999, cache_dir=d, fetch=f, sleep=lambda s: None)
            rd = tools.read_snapshot(URL, "19990508060549", cache_dir=d, fetch=f, sleep=lambda s: None)
        return [lst, rd]

    def test_matching_spoken_date_and_site_pass(self):
        r = self.results()
        for text in ('On May 8, 1999, example.test said "Tortoises are breeding this spring season."',
                     'On the eighth of May, nineteen ninety-nine, I said "Tortoises are breeding this spring".',
                     'May eighth, 1999. "Another synthetic sentence of text here."',
                     'I hold captures from October 10, 1999 and May 8, 1999.'):
            with self.subTest(text=text):
                self.assertEqual(check_turn(text, r, spirit="wayback"), [])

    def test_wrong_spoken_date_is_flagged(self):
        r = self.results()
        for text in ('On May 9, 1999, I said "Tortoises are breeding this spring season."',
                     'On June 8, 1999 I said "Tortoises are breeding this spring season."',
                     'On May 8, 2000 I said "Tortoises are breeding this spring season."',
                     'On the ninth of May nineteen ninety-nine I said "Tortoises are breeding this spring".',
                     'In June of two thousand I said "Tortoises are breeding this spring season."',
                     'In 1998 I said "Tortoises are breeding this spring season."'):
            with self.subTest(text=text):
                self.assertTrue(any("date" in p or "year" in p for p in check_turn(text, r, spirit="wayback")), text)

    def test_wrong_site_is_flagged(self):
        r = self.results()
        self.assertTrue(any("site name" in p for p in check_turn(
            'On May 8, 1999, other.com said "Tortoises are breeding this spring season."', r, spirit="wayback")))

    def test_spoken_timestamp_variants(self):
        r = self.results()
        ok = "one nine nine nine zero five zero eight zero six zero five four nine"
        self.assertEqual(check_turn(f"At {ok}.", r, spirit="wayback"), [])
        self.assertEqual(check_turn("At 19990508060549.", r), [])
        bad = ["At 19990508060540.", "At one nine nine nine zero five zero eight zero six zero five five zero.",
               "At 1999 0508 0605 40.", "At one nine nine nine zero five zero nine."]
        for text in bad:
            with self.subTest(text=text):
                self.assertTrue(check_turn(text, r, spirit="wayback"), text)
        # a partial run that is a real part of the timestamp passes
        self.assertEqual(check_turn("At one nine nine nine zero five zero eight.", r, spirit="wayback"), [])

    def test_large_spoken_number_cannot_be_checked(self):
        r = self.results()
        self.assertTrue(check_turn("At nineteen million nine hundred thousand.", r, spirit="wayback"))

    def test_existing_url_and_digit_checks_still_work(self):
        r = self.results()
        self.assertEqual(check_turn("See http://www.example.test:80/ please.", r), [])
        self.assertTrue(check_turn("See http://www.other.test/ please.", r))
        self.assertTrue(check_turn("At 20010101000000.", r))

    def test_other_spirit_results_without_timestamps_are_not_date_checked(self):
        enron = [{"source": "x", "content": "Meet on May 9, 2001.", "summary": "s", "evidence": []}]
        self.assertEqual(check_turn("The email says meet on May 9, 2001, at enron.com.", enron), [])

    def test_date_mentions_parser(self):
        self.assertEqual(date_mentions("June 17, 2000"), [(6, 17, 2000)])
        self.assertEqual(date_mentions("the seventeenth of June, two thousand"), [(6, 17, 2000)])
        self.assertEqual(date_mentions("June twenty-first nineteen ninety-nine"), [(6, 21, 1999)])
        self.assertEqual(date_mentions("in June of two thousand and one"), [(6, None, 2001)])
        self.assertEqual(date_mentions("June 2000"), [(6, None, 2000)])
        self.assertEqual(date_mentions("it was June, a quiet month"), [])
        self.assertEqual(date_mentions("may I help"), [])

    def test_digit_runs(self):
        self.assertEqual(digit_runs("code 12345678 ok"), ["12345678"])
        self.assertEqual(digit_runs("in 1999, 2000 and 2001"), [])
        self.assertEqual(digit_runs("one two three"), [])


class QuoteTighteningTest(unittest.TestCase):
    CONTENT = [{"source": "s", "content": "Let's hear it for papadogs. Some make great moms.\nDown the Hatch",
                "summary": "x", "evidence": []}]

    def test_three_and_four_word_quotes_are_now_checked(self):
        self.assertEqual(quoted_spans('He said "one two three" and "a b".'), ["one two three"])
        self.assertTrue(check_turn('It says "pill the dog".', self.CONTENT))
        self.assertEqual(check_turn('It says "hear it for papadogs".', self.CONTENT), [])

    def test_one_and_two_word_quotes_stay_unchecked(self):
        self.assertEqual(check_turn('The "Down the" heading and "Hatch".', self.CONTENT), [])

    def test_typographic_quotes_dashes_and_ellipsis_do_not_cause_false_violations(self):
        content = [{"source": "s", "content": "Don't waste inside space\u2014let Kitty enjoy it.",
                    "summary": "x", "evidence": []}]
        self.assertEqual(check_turn("It says \u201cDon\u2019t waste inside space\u2014let Kitty enjoy it.\u201d", content), [])
        self.assertEqual(check_turn('It says "Don\'t waste inside space ... let Kitty enjoy it".', content), [])
        self.assertTrue(check_turn('It says "Do waste inside space".', content))


class MannerFactLineTest(unittest.TestCase):
    R = [{"source": "s", "content": "Pets.Com : Home Page\nPaternal Instincts", "summary": "x",
          "evidence": [{"timestamp": "20000617112828"}]}]

    def probs(self, text):
        return check_turn(text, self.R, spirit="wayback")

    def test_theatrical_manner_without_facts_passes(self):
        self.assertEqual(self.probs("Out of the archive I rise, and I whisper: \"Pets.Com : Home Page\"."), [])
        self.assertEqual(self.probs("Digging through the archive."), [])

    def test_invented_fate_and_looks_are_flagged(self):
        for text in ("I went bankrupt soon after.", "I died in the dot-com crash.", "The company was acquired.",
                     "My blue banner was loud.", "The layout had a logo.", "I looked cheerful."):
            with self.subTest(text=text):
                self.assertTrue(self.probs(text), text)

    def test_the_same_words_inside_a_quote_are_not_flagged(self):
        r = [{"source": "s", "content": "Photo of the Day is a blue banner link", "summary": "x", "evidence": []}]
        self.assertEqual(check_turn('It says "Photo of the Day is a blue banner".', r, spirit="wayback"), [])

    def test_other_spirits_are_not_style_checked(self):
        self.assertEqual(check_turn("The company went bankrupt.", self.R), [])

    def test_cold_turn_is_exact(self):
        self.assertEqual(check_turn("The trail goes cold.", self.R, after_cold=True, spirit="wayback"), [])
        self.assertTrue(check_turn("The trail goes cold, friend.", self.R, after_cold=True, spirit="wayback"))


class PersonaTest(unittest.TestCase):
    def test_persona_encodes_the_manner_fact_line(self):
        p = spirit.PERSONA
        for needle in ("spoken_date", "spoken_site", "Never say the 14-digit timestamp aloud",
                       "The trail goes cold.", "MANNER", "FACTS", "at least three words",
                       "Never say when or why the site ended"):
            self.assertIn(needle, p)


if __name__ == "__main__":
    unittest.main()
