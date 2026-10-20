"""store: hourly/recent accumulation, latency weighting, outage bookkeeping, pruning."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest

from statusd import store
from statusd.live import Live

NOW = 1792479000
HOUR = store.floor_hour(NOW)


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.conn = store.connect(os.path.join(tmp.name, "history.db"))
        self.addCleanup(self.conn.close)

    def hourly(self, cid: str = "site", vantage: str = "droplet", hour: int = HOUR):
        return self.conn.execute(
            "SELECT samples, ok, latency_ms FROM hourly WHERE cid=? AND vantage=? AND hour=?",
            (cid, vantage, hour)).fetchone()

    def buckets(self, cid: str = "site", vantage: str = "droplet"):
        return [r[0] for r in self.conn.execute(
            "SELECT bucket FROM recent WHERE cid=? AND vantage=? ORDER BY bucket", (cid, vantage))]

    def outages(self, cid: str = "site", vantage: str = "droplet"):
        return [tuple(r) for r in self.conn.execute(
            'SELECT start, "end" FROM outage WHERE cid=? AND vantage=? ORDER BY start', (cid, vantage))]

    def live(self) -> Live:
        return Live(self.conn, down_after_failures=2)

    # ── accumulation ─────────────────────────────────────────────────────────

    def test_two_flushes_in_one_hour_sum(self):
        acc: store.Accumulator = {}
        store.add(acc, "site", "droplet", HOUR + 60, True, 100.0)
        store.add(acc, "site", "droplet", HOUR + 90, True, 100.0)
        store.add(acc, "site", "droplet", HOUR + 120, False, None)
        store.flush(self.conn, acc, HOUR + 130)
        store.add(acc, "site", "droplet", HOUR + 300, True, 200.0)
        store.add(acc, "site", "droplet", HOUR + 330, True, 200.0)
        store.flush(self.conn, acc, HOUR + 340)

        samples, ok, latency = self.hourly()
        self.assertEqual((samples, ok), (5, 4))
        self.assertAlmostEqual(latency, 150.0)  # (100+100+200+200)/4, the failure contributes nothing

    def test_latency_mean_ignores_failed_samples(self):
        acc: store.Accumulator = {}
        store.add(acc, "site", "droplet", HOUR, True, 100.0)
        store.add(acc, "site", "droplet", HOUR + 30, False, None)
        store.add(acc, "site", "droplet", HOUR + 60, True, 300.0)
        store.flush(self.conn, acc, HOUR + 90)
        self.assertEqual(self.hourly(), (3, 2, 200.0))

    def test_latency_is_null_when_nothing_succeeded(self):
        acc: store.Accumulator = {}
        store.add(acc, "site", "droplet", HOUR, False, None)
        store.add(acc, "site", "droplet", HOUR + 30, False, None)
        store.flush(self.conn, acc, HOUR + 60)
        self.assertEqual(self.hourly(), (2, 0, None))

    def test_recent_buckets_older_than_48h_are_pruned(self):
        acc: store.Accumulator = {}
        inside = store.floor_10m(NOW) - store.RECENT_KEEP_S + 600  # oldest bucket that survives
        stale = inside - 1200
        recent = store.floor_10m(NOW) - 600
        for bucket in (stale, inside, recent):
            store.add(acc, "site", "droplet", bucket + 30, True, 10.0)
        store.flush(self.conn, acc, NOW)
        self.assertEqual(self.buckets(), [inside, recent])

    def test_concurrent_records_keep_every_sample(self):
        live = self.live()
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            try:
                for n in range(25):
                    live.record("site", "droplet", HOUR + n * 30, i % 2 == 0, 10.0)
            except BaseException as e:  # a raise inside a thread would otherwise pass silently
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        live.flush(store.floor_hour(NOW) + 3600)
        self.assertEqual(self.hourly(), (200, 100, 10.0))

    # ── outages ──────────────────────────────────────────────────────────────

    def test_a_single_failure_opens_no_outage(self):
        live = self.live()
        live.record("site", "droplet", NOW - 30, False, None)
        self.assertEqual(self.outages(), [])
        self.assertIs(live.tracks[("site", "droplet")].last_ok, False)

    def test_outage_opens_at_the_first_failure_of_the_streak(self):
        live = self.live()
        live.record("site", "droplet", NOW - 60, False, None)
        live.record("site", "droplet", NOW - 30, False, None)
        # start is the first failure, not the one that tripped the threshold
        self.assertEqual(self.outages(), [(NOW - 60, None)])

    def test_success_closes_the_open_outage(self):
        live = self.live()
        live.record("site", "droplet", NOW - 60, False, None)
        live.record("site", "droplet", NOW - 30, False, None)
        live.record("site", "droplet", NOW, True, 12.0)
        self.assertEqual(self.outages(), [(NOW - 60, NOW)])
        self.assertIsNone(live.tracks[("site", "droplet")].open_start)

    def test_failure_success_failure_opens_no_outage(self):
        live = self.live()
        live.record("site", "droplet", NOW - 90, False, None)
        live.record("site", "droplet", NOW - 60, True, 12.0)
        live.record("site", "droplet", NOW - 30, False, None)
        self.assertEqual(self.outages(), [])

    def test_close_dangling_ends_an_open_outage_at_the_last_flush(self):
        start = NOW - 7200
        self.conn.execute("INSERT INTO outage VALUES ('site','droplet',?,NULL)", (start,))
        self.conn.execute("INSERT INTO outage VALUES ('site','droplet',?,?)", (start - 7200, start - 3600))
        store.meta_set(self.conn, "last_flush", str(start + 100))
        store.close_dangling(self.conn, NOW)
        self.assertEqual(self.outages(), [(start - 7200, start - 3600), (start, start + 100)])

    def test_close_dangling_never_ends_before_the_start(self):
        start = NOW - 7200
        self.conn.execute("INSERT INTO outage VALUES ('site','droplet',?,NULL)", (start,))
        store.meta_set(self.conn, "last_flush", str(start - 500))  # a crash before the outage opened
        store.close_dangling(self.conn, NOW)
        self.assertEqual(self.outages(), [(start, start)])


if __name__ == "__main__":
    unittest.main()
