"""Budgets and safety rules for the static design files served from designs/."""
import json
import re
import unittest
from pathlib import Path

DESIGNS = Path(__file__).resolve().parent.parent / "designs"
SERVED = ("*.html", "*.json", "*.js", "*.css")
QUAD = re.compile(r"(?<![\w.v])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.-])")
TOP_KEYS = {"v", "source", "scenario", "generated", "tz", "probe_interval_s", "home_age_s", "sample_age_s",
            "days_start", "hours_start", "latency_start", "groups", "layout", "providers", "sites", "nodes",
            "workloads", "components", "hops", "stations", "routes", "stats", "outages"}


def served():
    return sorted(p for g in SERVED for p in DESIGNS.glob(g))


def public_ips(text):
    out = []
    for m in QUAD.finditer(text):
        o = [int(x) for x in m.groups()]
        if any(x > 255 for x in o):
            continue
        if o[0] in (10, 127) or (o[0] == 192 and o[1] == 168):
            continue
        out.append(m.group(0))
    return out


class DesignBudgets(unittest.TestCase):
    def test_demo_pages_under_150k(self):
        for p in DESIGNS.glob("demo-*.html"):
            self.assertLessEqual(p.stat().st_size, 150_000, p.name)

    def test_shared_files_under_budget(self):
        for name, cap in (("estate-sim.js", 40_000), ("estate-core.js", 40_000), ("estate-core.css", 15_000)):
            self.assertLessEqual((DESIGNS / name).stat().st_size, cap, name)

    def test_configmap_total_under_900k(self):
        # the chart puts every served file into one ConfigMap (1 MiB hard cap)
        self.assertLessEqual(sum(p.stat().st_size for p in served()), 900_000)

    def test_json_naming(self):
        # designs/sample-*.json is fed to the v1 renderer by test_render.py
        for p in DESIGNS.glob("*.json"):
            self.assertTrue(p.name.startswith(("sample-", "estate-")), p.name)

    def test_no_public_ip(self):
        for p in served():
            self.assertEqual(public_ips(p.read_text(encoding="utf-8")), [], p.name)

    def test_snapshot_shape(self):
        p = DESIGNS / "estate-real.json"
        if not p.exists():
            self.skipTest("no snapshot committed")
        d = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(d["v"], 2)
        self.assertEqual(d["source"], "snapshot")
        self.assertLessEqual(TOP_KEYS, set(d))

    def test_demo_skeleton(self):
        for p in DESIGNS.glob("demo-*.html"):
            t = p.read_text(encoding="utf-8")
            for needle in ("estate-core.js", 'id="plain"', "<noscript>"):
                self.assertIn(needle, t, f"{p.name}: {needle}")


class Sanitiser(unittest.TestCase):
    def test_detects(self):
        self.assertEqual(public_ips("x 8.8.8.8 y"), ["8.8.8.8"])

    def test_allows_private_and_versions(self):
        self.assertEqual(public_ips("192.168.5.9 10.99.0.2 127.0.0.1 v1.2.3.4 1.2.3.4-rc 300.1.1.1"), [])


if __name__ == "__main__":
    unittest.main()
