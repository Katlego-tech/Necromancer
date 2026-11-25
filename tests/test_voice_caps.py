"""CapStore and Limits."""
import datetime
import json
import os
import tempfile
import threading
import unittest

from voice.guard import CapStore, Limits


class LimitsTest(unittest.TestCase):
    def test_bounds(self):
        Limits(max_session_seconds=480)
        for kw in ({"max_session_seconds": 481}, {"max_session_seconds": 0}, {"idle_seconds": 0},
                   {"daily_cap": -1}, {"total_cap": -1}):
            with self.assertRaises(ValueError):
                Limits(**kw)
        self.assertEqual(Limits().max_session_seconds, 180)


class CapStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "var", "caps.json")

    def test_threads_grant_exactly_the_cap(self):
        store = CapStore(self.path, Limits(daily_cap=5, total_cap=100))
        got = []

        def go():
            got.append(store.try_acquire())
        ts = [threading.Thread(target=go) for _ in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(1 for ok, _ in got if ok), 5)
        self.assertTrue(all(r == "daily_cap" for ok, r in got if not ok))

    def test_two_objects_one_file(self):
        lim = Limits(daily_cap=4, total_cap=100)
        a, b = CapStore(self.path, lim), CapStore(self.path, lim)
        got = []

        def go(store):
            for _ in range(6):
                got.append(store.try_acquire()[0])
        ts = [threading.Thread(target=go, args=(s,)) for s in (a, b)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sum(got), 4)

    def test_state_survives_restart_and_total_cap(self):
        lim = Limits(daily_cap=10, total_cap=2)
        CapStore(self.path, lim).try_acquire()
        CapStore(self.path, lim).try_acquire()
        self.assertEqual(CapStore(self.path, lim).try_acquire(), (False, "total_cap"))
        self.assertEqual(CapStore(self.path, lim).snapshot()["total_remaining"], 0)

    def test_utc_day_rollover(self):
        clock = [datetime.datetime(2026, 12, 1, 23, 59, tzinfo=datetime.timezone.utc)]
        store = CapStore(self.path, Limits(daily_cap=1, total_cap=10), now=lambda: clock[0])
        self.assertEqual(store.try_acquire(), (True, None))
        self.assertEqual(store.try_acquire(), (False, "daily_cap"))
        clock[0] = datetime.datetime(2026, 12, 2, 0, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(store.try_acquire(), (True, None))
        data = json.load(open(self.path))
        self.assertEqual(data, {"total": 2, "day": "2026-12-02", "day_count": 1,
                                "judge_total": 0, "judge_day_count": 0})

    def test_corrupt_file_fails_closed_and_is_kept(self):
        os.makedirs(os.path.dirname(self.path))
        for content in ("{not json", '{"total":"x","day":"d","day_count":0}', "[]"):
            with open(self.path, "w") as f:
                f.write(content)
            store = CapStore(self.path)
            self.assertEqual(store.try_acquire(), (False, "total_cap"))
            self.assertEqual(open(self.path).read(), content)

    def test_snapshot_starts_at_full(self):
        self.assertEqual(CapStore(self.path, Limits(daily_cap=3, total_cap=7)).snapshot(),
                         {"daily_remaining": 3, "total_remaining": 7})


if __name__ == "__main__":
    unittest.main()
