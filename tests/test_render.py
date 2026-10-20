"""render_page(): the shipped page for every committed fixture model."""
from __future__ import annotations

import json
import unittest
from html.parser import HTMLParser
from pathlib import Path

from statusd.render import render_page

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = sorted((ROOT / "tests" / "fixtures").glob("model-*.json"))
SAMPLES = sorted((ROOT / "designs").glob("sample-*.json"))

MAX_BYTES = 120_000
FORBIDDEN = ("<script", "192.168.", ".local", ".svc", "http://")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
        "track", "wbr"}

# What the headline must say per scenario, and what the body must show with it.
EXPECTED = {
    "model-normal": {"headline": "All systems operational"},
    "model-incident": {"headline": "1 service down"},
    "model-home-offline": {
        "headline": "2 services down",
        "text": ("Home monitor last reported", "home services show as unknown", ">unknown<"),
    },
    "model-stale": {"headline": "Status checks are stale", "text": ("Outside checks last ran",)},
}


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
        if not self.stack:
            self.errors.append(f"</{tag}> with nothing open")
        elif self.stack[-1] != tag:
            self.errors.append(f"</{tag}> while <{self.stack[-1]}> is open")
        else:
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
                self.assertEqual(checker.counts.get("details"), len(self.models[name]["components"]))
                self.assertNotIn("script", checker.counts)

    def test_headline_per_scenario(self):
        for name, page in self.pages.items():
            with self.subTest(scenario=name):
                headline = EXPECTED[name]["headline"]
                self.assertIn(headline, page)
                for text in EXPECTED[name].get("text", ()):
                    self.assertIn(text, page)

    @unittest.skipUnless(SAMPLES, "designs/sample-*.json arrive with another PR")
    def test_design_samples_render(self):
        for path in SAMPLES:
            with self.subTest(sample=path.name):
                model = json.loads(path.read_text())
                page = render_page(model, "preview")
                self.assertEqual(page.count("<details>"), len(model["components"]))


if __name__ == "__main__":
    unittest.main()
