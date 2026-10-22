"""render_page(): the shipped page for every committed fixture model."""
from __future__ import annotations

import json
import unittest
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

from statusd.render import BARS, render_page

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("model-*.json"))
SAMPLES = sorted((ROOT / "designs").glob("sample-*.json"))

MAX_BYTES = 120_000
FORBIDDEN = ("<script", "192.168.", ".local", ".svc", "http://")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
        "track", "wbr", "use", "line"}

# What the headline must say per scenario, and what the body must show with it.
EXPECTED = {
    "model-normal": {"headline": "All systems operational", "class": "ok"},
    "model-incident": {"headline": "1 service down", "class": "bad"},
    "model-home-offline": {
        "headline": "2 services down",
        "class": "bad",
        "text": ("Home monitor last reported", "home services show as unknown", ">awaiting report<"),
    },
    "model-stale": {"headline": "Status checks are stale", "class": "warn",
                    "text": ("Outside checks last ran",)},
}


def bar_class(p: float | None) -> str:
    """The contract the strip draws: green, amber, red, or 'nobody reported'."""
    if p is None:
        return "b-none"
    if p >= 99.9:
        return "b-ok"
    if p >= 99:
        return "b-minor"
    return "b-major"


def pre_history(model: dict) -> int:
    """How many of the 90 bars predate the first sample this page ever had."""
    tz = ZoneInfo(model["tz"])
    first = date.fromisoformat(model["days_start"])
    stamps = [c["since"] for c in model["components"] if c["since"] is not None]
    if not stamps:
        return 0
    return max(0, min(BARS, (datetime.fromtimestamp(min(stamps), tz).date() - first).days))


class _Checker(HTMLParser):
    """Collects unbalanced tags and a tag census; a browser would repair the former silently."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.counts: dict[str, int] = {}

    def handle_starttag(self, tag, attrs):
        self.counts[tag] = self.counts.get(tag, 0) + 1
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.counts[tag] = self.counts.get(tag, 0) + 1

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}>")
            return
        self.stack.pop()


class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = {p.stem: json.loads(p.read_text()) for p in FIXTURES}
        cls.pages = {name: render_page(m, "preview") for name, m in cls.models.items()}

    def test_the_committed_fixtures_are_all_covered(self):
        self.assertTrue(self.models, "no tests/fixtures/model-*.json to render")
        self.assertEqual(set(self.models), set(EXPECTED))

    def test_no_script_no_internal_names_no_plain_http(self):
        for name, page in self.pages.items():
            for bad in FORBIDDEN:
                with self.subTest(scenario=name, forbidden=bad):
                    self.assertNotIn(bad, page)

    def test_page_stays_small(self):
        for name, page in self.pages.items():
            with self.subTest(scenario=name):
                self.assertLess(len(page), MAX_BYTES)

    def test_markup_parses_balanced_and_lists_every_component(self):
        for name, page in self.pages.items():
            checker = _Checker()
            with self.subTest(scenario=name):
                checker.feed(page)
                checker.close()
                self.assertEqual(checker.errors, [])
                self.assertEqual(checker.stack, [], "unclosed tags")
                self.assertEqual(page.count('class="nm"'), len(self.models[name]["components"]))
                self.assertEqual(checker.counts.get("details", 0), 0)
                self.assertNotIn("script", checker.counts)

    def test_headline_per_scenario(self):
        for name, page in self.pages.items():
            with self.subTest(scenario=name):
                self.assertIn(EXPECTED[name]["headline"], page)
                self.assertIn(f'class="headline {EXPECTED[name]["class"]}"', page)
                for text in EXPECTED[name].get("text", ()):
                    self.assertIn(text, page)

    def test_every_row_carries_a_state_pill(self):
        for name, page in self.pages.items():
            with self.subTest(scenario=name):
                states = [c["state"] for c in self.models[name]["components"]]
                pills = sum(page.count(f'class="pill {cls}"') for cls in
                            ("ok", "warn", "bad", "muted"))
                self.assertGreaterEqual(pills, len(states))

    def test_strip_draws_one_bar_per_day(self):
        for name, page in self.pages.items():
            with self.subTest(scenario=name):
                n = len(self.models[name]["components"])
                self.assertEqual(page.count('<use href="#bar"'), n * BARS)

    def test_days_before_the_record_are_not_drawn_as_silence(self):
        """A day that predates the record and a day nobody reported must not look alike."""
        for name, model in self.models.items():
            page = self.pages[name]
            pre = pre_history(model)
            want = {"b-pre": 0, "b-none": 0, "b-ok": 0, "b-minor": 0, "b-major": 0}
            for c in model["components"]:
                for i, p in enumerate(c["daily_pct"]):
                    want["b-pre" if i < pre else bar_class(p)] += 1
            with self.subTest(scenario=name):
                for cls, n in want.items():
                    self.assertEqual(page.count(f'class="{cls}"'), n, cls)

    def test_the_page_states_how_much_history_it_has(self):
        for name, model in self.models.items():
            with self.subTest(scenario=name):
                self.assertIn("of history · since", self.pages[name])

    def test_summary_counts_match_the_model(self):
        for name, model in self.models.items():
            first = model["groups"][0]["id"]
            comps = [c for c in model["components"] if c["group"] == first]
            up = sum(c["state"] == "up" for c in comps)
            with self.subTest(scenario=name):
                self.assertIn(f"<b>{up} / {len(comps)}</b>{model['groups'][0]['name']}",
                              self.pages[name])

    def test_an_ongoing_outage_is_announced_and_not_called_resolved(self):
        for name, model in self.models.items():
            page = self.pages[name]
            live = [o for o in model["outages"] if o["end"] is None]
            with self.subTest(scenario=name):
                if live:
                    self.assertIn("is down, ongoing for", page)
                else:
                    self.assertNotIn("is down, ongoing for", page)
                self.assertNotIn('<span class="pill ok">ongoing</span>', page)

    @unittest.skipUnless(SAMPLES, "designs/sample-*.json arrive with another PR")
    def test_design_samples_render(self):
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                model = json.loads(path.read_text())
                page = render_page(model, "preview")
                self.assertEqual(page.count('class="nm"'), len(model["components"]))


if __name__ == "__main__":
    unittest.main()
