"""build_model(): schema shape, vantage precedence, state rules, outages, daily aggregation."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from statusd import config, store
from statusd.live import Live
from statusd.model import build_model, day_bounds

ROOT = Path(__file__).resolve().parent.parent
STATUSD_TOML = ROOT / "config" / "statusd.toml"
COMPONENTS_TOML = ROOT / "config" / "components.toml"

NOW = 1792479000  # 2026-10-20 02:50 EDT
TZ = "America/New_York"

TOP_KEYS = ["v", "generated", "tz", "probe_interval_s", "home_age_s", "groups", "days_start",
            "hours_start", "latency_start", "components", "outages", "sample_age_s"]
COMPONENT_KEYS = ["id", "name", "group", "hosted", "state", "latency_ms", "cert_days", "uptime",
                  "since", "daily_pct", "daily_down_min", "hourly_pct", "latency_10m"]

SYNTHETIC_COMPONENTS = """
group = [
  { id = "web", name = "Websites", note = "Checked from outside." },
  { id = "home", name = "Home services", note = "Reported from home." },
]

component = [
  { id = "site", name = "Site", group = "web", kind = "http", hosted = "cloud", url = "https://site.test" },
  { id = "site2", name = "Site two", group = "web", kind = "http", hosted = "home", url = "https://site2.test" },
  { id = "box", name = "Box", group = "home", kind = "lan", hosted = "home" },
  { id = "box2", name = "Box two", group = "home", kind = "lan", hosted = "home" },
]
"""


class _Case(unittest.TestCase):
    components_path: Path | None = None  # None -> SYNTHETIC_COMPONENTS

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        if self.components_path is None:
            self.components_path = self.dir / "components.toml"
            self.components_path.write_text(SYNTHETIC_COMPONENTS)
        self.cfg = config.load(str(STATUSD_TOML), str(self.components_path))
        self.conn = store.connect(str(self.dir / "history.db"))
        self.addCleanup(self.conn.close)
        self.live = Live(self.conn, self.cfg.down_after_failures)

    def model(self, now: int = NOW) -> dict:
        return build_model(self.conn, self.cfg, self.live, now)

    def comp(self, model: dict, cid: str) -> dict:
        return next(c for c in model["components"] if c["id"] == cid)

    def hourly(self, hour: int, vantage: str = "droplet", samples: int = 100,
               ok: int | None = None, latency: float | None = None, cid: str = "site") -> None:
        self.conn.execute("INSERT INTO hourly VALUES (?,?,?,?,?,?)",
                          (cid, vantage, hour, samples, samples if ok is None else ok, latency))

    def seed_states(self, records: list[tuple[int, bool]]) -> None:
        for ts, ok in records:
            self.live.record("site", "droplet", ts, ok, 12.0 if ok else None)


class SchemaTest(_Case):
    """Key names and order are the design samples' `v: 1` schema; nothing else is added."""
    components_path = COMPONENTS_TOML

    def setUp(self):
        super().setUp()
        self.m = self.model()

    def test_top_level_keys_and_order(self):
        self.assertEqual(list(self.m), TOP_KEYS)
        self.assertEqual((self.m["v"], self.m["generated"], self.m["tz"]), (1, NOW, TZ))
        self.assertEqual([g["id"] for g in self.m["groups"]], [g.id for g in self.cfg.groups])
        self.assertEqual([list(g) for g in self.m["groups"]], [["id", "name", "note"]] * 3)
        self.assertEqual([c["id"] for c in self.m["components"]],
                         [c.id for c in self.cfg.components])

    def test_component_keys_and_order(self):
        for c, src in zip(self.m["components"], self.cfg.components):
            want = COMPONENT_KEYS + (["url"] if src.kind == "http" else [])
            with self.subTest(component=c["id"]):
                self.assertEqual(list(c), want)
                if src.kind == "http":
                    self.assertEqual(c["url"], src.url)
        self.assertEqual(list(self.comp(self.m, "john2143-com")["uptime"]),
                         ["24h", "7d", "30d", "90d"])

    def test_series_lengths(self):
        for c in self.m["components"]:
            with self.subTest(component=c["id"]):
                self.assertEqual(len(c["daily_pct"]), 90)
                self.assertEqual(len(c["daily_down_min"]), 90)
                self.assertEqual(len(c["hourly_pct"]), 168)
                self.assertEqual(len(c["latency_10m"]), 144)

    def test_window_anchors(self):
        self.assertEqual(self.m["days_start"], "2026-07-23")
        self.assertEqual(self.m["hours_start"], 1791874800)
        self.assertEqual(self.m["latency_start"], 1792393200)

    def test_an_empty_database_has_no_history(self):
        for c in self.m["components"]:
            self.assertIsNone(c["since"])
            self.assertEqual(set(c["daily_pct"]), {None})
            self.assertEqual(set(c["uptime"].values()), {None})


class HistoryTest(_Case):
    def hour_pct(self, model: dict, cid: str, hour: int):
        return self.comp(model, cid)["hourly_pct"][(hour - model["hours_start"]) // 3600]

    def test_pite_backfill_fills_hours_the_droplet_has_none_for(self):
        pite_hour = store.floor_hour(NOW) - 3 * 3600
        droplet_hour = store.floor_hour(NOW) - 2 * 3600
        self.hourly(pite_hour, "pite", 100, 50)
        self.hourly(droplet_hour, "droplet", 100, 100)
        m = self.model()
        self.assertEqual(self.hour_pct(m, "site", pite_hour), 50.0)
        self.assertEqual(self.hour_pct(m, "site", droplet_hour), 100.0)
        # the merged series is what the uptime windows read
        self.assertEqual(self.comp(m, "site")["uptime"]["24h"], 75.0)

    def test_a_droplet_hour_wins_over_a_pite_hour(self):
        hour = store.floor_hour(NOW) - 3600
        self.hourly(hour, "pite", 100, 0)
        self.hourly(hour, "droplet", 100, 100)
        m = self.model()
        self.assertEqual(self.hour_pct(m, "site", hour), 100.0)
        self.assertEqual(self.comp(m, "site")["uptime"]["24h"], 100.0)

    def test_a_lan_component_never_reads_droplet_rows(self):
        hour = store.floor_hour(NOW) - 3600
        self.hourly(hour, "droplet", 100, 100, cid="box")
        self.conn.execute("INSERT INTO recent VALUES ('box','droplet',?,20,20,5.0)",
                          (store.floor_10m(NOW) - 600,))
        c = self.comp(self.model(), "box")
        self.assertIsNone(c["since"])
        self.assertEqual(set(c["hourly_pct"]), {None})
        self.assertEqual(set(c["uptime"].values()), {None})
        self.assertEqual(set(c["latency_10m"]), {None})

        self.hourly(hour, "pite", 10, 10, cid="box")
        c = self.comp(self.model(), "box")
        self.assertEqual(c["since"], hour)
        self.assertEqual(c["uptime"]["24h"], 100.0)


class StateTest(_Case):
    STATES = (
        ("fresh_success", [(NOW - 10, True)], "up"),
        ("at_the_stale_boundary", [(NOW - 120, True)], "up"),
        ("one_second_past_the_boundary", [(NOW - 121, True)], "unknown"),
        ("two_failures_open_an_outage", [(NOW - 40, False), (NOW - 10, False)], "down"),
        ("one_failure_inside_the_window", [(NOW - 300, True), (NOW - 200, False), (NOW - 100, True)],
         "degraded"),
        ("a_failure_outside_the_window", [(NOW - 700, False), (NOW - 10, True)], "up"),
    )

    def test_state_rules(self):
        for name, records, want in self.STATES:
            with self.subTest(case=name):
                self.live = Live(self.conn, self.cfg.down_after_failures)  # no state from the case before
                self.seed_states(records)
                self.assertEqual(self.comp(self.model(), "site")["state"], want)

    def test_latency_is_shown_only_for_a_fresh_success(self):
        self.live.record("site", "droplet", NOW - 10, True, 12.34)
        self.assertEqual(self.comp(self.model(), "site")["latency_ms"], 12.3)
        self.live.record("site", "droplet", NOW - 5, False, None)
        self.assertIsNone(self.comp(self.model(), "site")["latency_ms"])

    def test_one_lan_component_goes_unknown_while_the_others_stay_fresh(self):
        self.live.record("site", "droplet", NOW - 10, True, 12.0)
        self.live.record("box", "pite", NOW - 10, True, 3.0)
        self.live.record("box2", "pite", NOW - 200, True, 3.0)
        states = {c["id"]: c["state"] for c in self.model()["components"]}
        self.assertEqual({cid: states[cid] for cid in ("site", "box", "box2")},
                         {"site": "up", "box": "up", "box2": "unknown"})

    def test_sample_age_is_the_newest_droplet_sample_and_home_age_is_the_report_age(self):
        # Both drive the page's banners and headline, so both must come from live state.
        self.assertIsNone(self.model()["sample_age_s"])
        self.live.record("site", "droplet", NOW - 300, True, 12.0)
        self.live.record("site2", "droplet", NOW - 100, True, 12.0)  # the newest outside check
        self.live.record("box", "pite", NOW - 5, True, 3.0)  # a home sample is not an outside check
        self.live.home_last_report = NOW - 860
        m = self.model()
        self.assertEqual(m["sample_age_s"], 100)
        self.assertEqual(m["home_age_s"], 860)


class OutageTest(_Case):
    START = NOW - 3600

    def outage(self, start: int, end: int | None) -> None:
        self.conn.execute("INSERT INTO outage VALUES ('site','droplet',?,?)", (start, end))

    def site_outages(self, model: dict) -> list[tuple[int, int | None]]:
        return [(o["start"], o["end"]) for o in model["outages"] if o["id"] == "site"]

    def test_outages_100_seconds_apart_merge(self):
        self.outage(self.START, self.START + 300)
        self.outage(self.START + 400, self.START + 600)
        self.assertEqual(self.site_outages(self.model()), [(self.START, self.START + 600)])

    def test_outages_200_seconds_apart_stay_separate(self):
        self.outage(self.START, self.START + 300)
        self.outage(self.START + 500, self.START + 700)
        self.assertEqual(self.site_outages(self.model()),
                         [(self.START + 500, self.START + 700), (self.START, self.START + 300)])


class DailyPctTest(_Case):
    def test_daily_down_min_is_null_exactly_where_daily_pct_is(self):
        for i in range(12):  # the last 12 hours: today and yesterday
            self.hourly(store.floor_hour(NOW) - i * 3600, "droplet", 100, 100)
        self.conn.execute("INSERT INTO outage VALUES ('site','droplet',?,?)", (NOW - 3600, NOW - 1800))
        c = self.comp(self.model(), "site")
        self.assertEqual([p is None for p in c["daily_pct"]],
                         [d is None for d in c["daily_down_min"]])
        self.assertIn(False, [p is None for p in c["daily_pct"]], "no day has samples")
        self.assertIn(True, [p is None for p in c["daily_pct"]], "no day is empty")
        self.assertEqual(c["daily_down_min"][-1], 30)  # the open-ended half hour inside today

    def _seed_day(self, day: date, ok_pattern) -> int:
        lo, hi = day_bounds(ZoneInfo(TZ), day)
        hours = list(range(lo, hi, 3600))
        for i, h in enumerate(hours):
            self.hourly(h, "droplet", 1, ok_pattern(i))
        self.hourly(hi, "droplet", 1, 0)  # the following local hour: a correct span excludes it
        return len(hours)

    def _day_pct(self, m: dict, day: date):
        idx = (day - date.fromisoformat(m["days_start"])).days
        return self.comp(m, "site")["daily_pct"][idx]

    def test_the_fall_back_day_aggregates_25_hours(self):
        day = date(2026, 11, 1)
        self.assertEqual(self._seed_day(day, lambda i: 0 if i == 12 else 1), 25)
        lo, hi = day_bounds(ZoneInfo(TZ), day)
        self.assertEqual((hi - lo) // 3600, 25)
        # 24 of 25 hours good: 23 h would give 100.0 or 95.652, 26 h would give 92.307
        self.assertEqual(self._day_pct(self.model(now=1793638800), day), 96.0)

    def test_the_spring_forward_day_aggregates_23_hours(self):
        day = date(2026, 3, 8)
        self.assertEqual(self._seed_day(day, lambda i: 0 if i == 11 else 1), 23)
        lo, hi = day_bounds(ZoneInfo(TZ), day)
        self.assertEqual((hi - lo) // 3600, 23)
        # 22 of 23 hours good: 24 h would include the failing extra hour (92.0), 22 h could not
        self.assertEqual(self._day_pct(self.model(now=1773072000), day), 95.652)


if __name__ == "__main__":
    unittest.main()
