"""check_alert tests: spoken alert vs the schedule record values."""
import unittest
from voice.grounding_check import check_alert, normalize_number_words

ARGS = ("4", "Sandton (Demo Area 1)", "6 PM on 30 November 2026")
SPIKE1 = ("Warning. Stage four load shedding in area Sandton Demo Area one starts at "
          "six PM on thirty November twenty twenty six.")
SPIKE2 = ("Warning. Stage four, area Sandton, Demo Area one, start time six PM on "
          "thirty November twenty twenty six.")


class CheckAlertTest(unittest.TestCase):
    def test_spike_paraphrases_pass(self):
        self.assertEqual(check_alert(SPIKE1, *ARGS), [])
        self.assertEqual(check_alert(SPIKE2, *ARGS), [])

    def test_digits_and_full_area_pass(self):
        t = "Warning. Stage 4; area Sandton (Demo Area 1); start time 6 PM on 30 November 2026."
        self.assertEqual(check_alert(t, *ARGS), [])

    def test_minutes_record(self):
        a = ("2", "Soweto (Demo Area 2)", "6:30 PM on 1 December 2026")
        ok = "Warning. Stage two, Soweto, Demo Area two, 6:30 PM on 1 December 2026."
        self.assertEqual(check_alert(ok, *a), [])
        self.assertTrue(check_alert(ok.replace("6:30", "6"), *a))

    def test_flags_wrong_values(self):
        for bad in (SPIKE2.replace("four", "five"),
                    SPIKE2.replace("Area one", "Area two"),
                    SPIKE2.replace("six PM", "seven PM"),
                    SPIKE2.replace("six PM", "six AM"),
                    SPIKE2.replace("thirty November", "twenty nine November"),
                    SPIKE2.replace("November", "December"),
                    SPIKE2.replace("twenty six.", "twenty five."),
                    SPIKE2.replace("Sandton", "Soweto")):
            self.assertTrue(check_alert(bad, *ARGS), bad)

    def test_flags_missing_values(self):
        self.assertTrue(check_alert("Warning. Stage four, Sandton, Demo Area one.", *ARGS))
        self.assertTrue(check_alert("Warning. Sandton Demo Area one, six PM on thirty November "
                                    "twenty twenty six.", *ARGS))

    def test_number_words(self):
        self.assertEqual(normalize_number_words("thirty one"), "31")
        self.assertEqual(normalize_number_words("twenty twenty six"), "2026")
        self.assertEqual(normalize_number_words("stage four"), "stage 4")


if __name__ == "__main__":
    unittest.main()
