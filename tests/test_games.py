"""Pure functions and registry invariants of scripts/games_snapshot.py."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import games_snapshot as gs  # noqa: E402

IDS = ["rots", "dino", "moss", "steam-lobby", "how-poe-sale",
       "factorio", "minecraft", "teamspeak", "openrct2", "pelican"]
SERVERS = {"factorio", "minecraft", "teamspeak", "openrct2", "pelican"}


def series(value, **labels):
    return {"metric": labels, "value": [0, str(value)]}


def svc(state, err=None, req=None):
    return {"state": state, "err_ratio": err, "req_rate": req}


def dep(name, available=0, unavailable=0):
    return {"name": name, "available": available, "unavailable": unavailable}


class ServiceState(unittest.TestCase):
    def test_branches(self):
        cases = (
            ((0, 0, True), "asleep"),
            ((0, 0, False), "off"),
            ((0, 2, False), "down"),
            ((0, 2, True), "down"),
            ((1, 2, False), "degraded"),
            ((1, 1, False), "up"),
            ((3, 2, False), "up"),
            ((None, 2, False), "unknown"),
            ((2, None, False), "unknown"),
        )
        for args, want in cases:
            self.assertEqual(gs.service_state(*args), want, args)


class EnvState(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(gs.env_state([svc("up"), svc("down")], None), "down")
        self.assertEqual(gs.env_state([svc("up"), svc("degraded")], None), "degraded")
        self.assertEqual(gs.env_state([svc("asleep"), svc("up")], None), "up")
        self.assertEqual(gs.env_state([svc("off"), svc("asleep")], None), "asleep")
        self.assertEqual(gs.env_state([svc("off")], None), "off")
        self.assertEqual(gs.env_state([svc("unknown"), svc("unknown")], None), "unknown")

    def test_error_ratio_downgrades_with_traffic(self):
        self.assertEqual(gs.env_state([svc("up", err=0.06, req=1.0)], None), "degraded")

    def test_error_ratio_ignored_below_a_trickle(self):
        self.assertEqual(gs.env_state([svc("up", err=1.0, req=0.01)], None), "up")

    def test_argo_unhealthy_downgrades(self):
        self.assertEqual(gs.env_state([svc("up")], "Degraded"), "degraded")
        self.assertEqual(gs.env_state([svc("up")], "Healthy"), "up")
        self.assertEqual(gs.env_state([svc("up")], None), "up")

    def test_unknown_argo_does_not_downgrade(self):
        self.assertEqual(gs.env_state([svc("up")], None), "up")


class GameState(unittest.TestCase):
    def test_prod_is_authoritative(self):
        self.assertEqual(gs.game_state([{"id": "prod", "state": "up"}, {"id": "dev", "state": "down"}]), "up")

    def test_worst_without_prod(self):
        self.assertEqual(gs.game_state([{"id": "a", "state": "asleep"}, {"id": "b", "state": "degraded"}]),
                         "degraded")

    def test_empty(self):
        self.assertEqual(gs.game_state([]), "unknown")


class PreviewState(unittest.TestCase):
    def test_inactive_namespace(self):
        self.assertIsNone(gs.preview_state(False, [dep("dino-dash-web", 1)]))

    def test_anubis_does_not_mask_a_broken_app(self):
        deps = [dep("anubis", 1), dep("dino-dash-web", 0, 1)]
        self.assertEqual(gs.preview_state(True, deps), "down")

    def test_anubis_alone_is_unknown(self):
        self.assertEqual(gs.preview_state(True, [dep("anubis", 1)]), "unknown")

    def test_up(self):
        self.assertEqual(gs.preview_state(True, [dep("anubis", 1), dep("rots-web", 1)]), "up")

    def test_asleep(self):
        self.assertEqual(gs.preview_state(True, [dep("rots-web", 0, 0)]), "asleep")

    def test_no_deployments(self):
        self.assertEqual(gs.preview_state(True, []), "unknown")


class Redaction(unittest.TestCase):
    def test_public_ip(self):
        self.assertEqual(gs.redact_ips("pin resolver to 8.8.8.8"), "pin resolver to x.x.x.x")

    def test_versions_and_private_untouched(self):
        self.assertEqual(gs.redact_ips("bump to 1.2.3.4-rc"), "bump to 1.2.3.4-rc")
        self.assertEqual(gs.redact_ips("192.168.5.9"), "192.168.5.9")

    def test_pr_title_redacted(self):
        entry = gs.pr_entry("2143-Labs/rots4", {"number": 3, "title": "pin resolver to 8.8.8.8"})
        self.assertEqual(entry["title"], "pin resolver to x.x.x.x")


class PrUrl(unittest.TestCase):
    def test_built_from_number(self):
        raw = {"number": 7, "title": "x", "draft": False,
               "html_url": "javascript:alert(1)", "updated_at": "2026-11-01T12:00:00Z"}
        entry = gs.pr_entry("2143-Labs/rots4", raw)
        self.assertEqual(entry["url"], "https://github.com/2143-Labs/rots4/pull/7")
        self.assertEqual(entry["updated"], 1793534400)
        self.assertFalse(entry["draft"])


class Registry(unittest.TestCase):
    def setUp(self):
        self.doc = gs.build({}, {}, now=0)

    def test_ids_and_order(self):
        self.assertEqual([g["id"] for g in self.doc["games"]], IDS)
        self.assertEqual(self.doc["v"], 1)
        self.assertEqual(self.doc["source"], "live")
        self.assertEqual(self.doc["interval_s"], 3)
        self.assertEqual(len({g["id"] for g in self.doc["games"]}), len(IDS))

    def test_repos_and_urls(self):
        for g in gs.GAMES:
            repo = g.get("repo")
            if repo is not None:
                self.assertRegex(repo, r"^2143-Labs/[A-Za-z0-9._-]+$", g["id"])
            if g["url"] is not None:
                self.assertTrue(g["url"].startswith("https://"), g["id"])
            for env in g["envs"]:
                self.assertTrue(env["ns"], g["id"])
                for s in env["services"]:
                    self.assertIn(s["probe"]["kind"], ("deployment", "statefulsets", "pods"))
                    self.assertEqual(len([k for k in ("name", "prefix") if k in s["probe"]]), 1,
                                     f"{g['id']}/{env['id']}/{s['name']}")

    def test_server_cards_have_no_pulls(self):
        for g in self.doc["games"]:
            if g["id"] in SERVERS:
                self.assertIsNone(g["repo"])
                self.assertEqual(g["prs"], [])


class LiveInstances(unittest.TestCase):
    def test_only_live_instances_counted(self):
        # rots-gs-1-g43 was destroyed: its series is gone, not zero.
        metrics = {"sts_ready": [series(1, namespace="rots-prod", statefulset="rots-gs-0-g56"),
                                 series(1, namespace="rots-prod", statefulset="rots-gs-2-g57")]}
        doc = gs.build(metrics, {}, now=0)
        rots = next(g for g in doc["games"] if g["id"] == "rots")
        self.assertEqual(rots["servers"], 2)
        game = next(s for s in rots["envs"][0]["services"] if s["name"] == "game servers")
        self.assertEqual((game["ready"], game["desired"]), (2, 2))

    def test_missing_series_is_null_not_zero(self):
        doc = gs.build({"dep_ready": [series(2, namespace="rots-prod", deployment="rots-web")]}, {}, now=0)
        rots = next(g for g in doc["games"] if g["id"] == "rots")
        auth = next(s for s in rots["envs"][0]["services"] if s["name"] == "authserver")
        self.assertIsNone(auth["ready"])
        self.assertEqual(auth["state"], "unknown")

    def test_beyla_rates_attach_to_deployments(self):
        metrics = {"dep_ready": [series(2, namespace="rots-prod", deployment="rots-authserver")],
                   "dep_desired": [series(2, namespace="rots-prod", deployment="rots-authserver")],
                   "http_5xx": [series(0.002, service_namespace="rots-prod", service_name="rots-authserver")],
                   "http_total": [series(1.0, service_namespace="rots-prod", service_name="rots-authserver")]}
        doc = gs.build(metrics, {}, now=0)
        rots = next(g for g in doc["games"] if g["id"] == "rots")
        auth = next(s for s in rots["envs"][0]["services"] if s["name"] == "authserver")
        self.assertEqual(auth["state"], "up")
        self.assertAlmostEqual(auth["err_ratio"], 0.002)
        self.assertAlmostEqual(auth["req_rate"], 1.0)

    def test_preview_from_namespace_phase(self):
        metrics = {"ns_active": [series(1, namespace="rots-pr-12")],
                   "dep_ready": [series(1, namespace="rots-pr-12", deployment="rots-web")],
                   "dep_unavail": [series(0, namespace="rots-pr-12", deployment="rots-web")]}
        raw = {"2143-Labs/rots4": [{"number": 12, "title": "t", "draft": False,
                                    "updated_at": "2026-11-01T12:00:00Z"}]}
        doc = gs.build(metrics, raw, now=0)
        rots = next(g for g in doc["games"] if g["id"] == "rots")
        self.assertEqual(rots["prs"][0]["preview"],
                         {"url": "https://rots-12.john2143.com", "ns": "rots-pr-12", "state": "up"})


class Sanitiser(unittest.TestCase):
    def test_detects(self):
        self.assertEqual(gs.public_ips("x 8.8.8.8 y"), ["8.8.8.8"])

    def test_allows_private_and_versions(self):
        self.assertEqual(gs.public_ips("192.168.5.9 10.99.0.2 127.0.0.1 v1.2.3.4 1.2.3.4-rc 300.1.1.1"), [])


class Sample(unittest.TestCase):
    def test_sample_matches_registry(self):
        p = ROOT / "designs" / "games-sample.json"
        if not p.exists():
            self.skipTest("designs/games-sample.json not written yet")
        d = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(d["v"], 1)
        self.assertEqual(d["source"], "sample")
        self.assertEqual({g["id"] for g in d["games"]}, set(IDS))


if __name__ == "__main__":
    unittest.main()
