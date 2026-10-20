"""config.load(): the committed configs, and every rejection path."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from statusd import config

ROOT = Path(__file__).resolve().parent.parent
STATUSD_TOML = ROOT / "config" / "statusd.toml"
COMPONENTS_TOML = ROOT / "config" / "components.toml"

SITE = {"id": "site", "name": "Site", "group": "web", "kind": "http",
        "hosted": "cloud", "url": "https://site.test"}
BOX = {"id": "box", "name": "Box", "group": "web", "kind": "lan", "hosted": "home"}


def row(**fields) -> str:
    """One TOML component row; JSON is valid TOML for strings and string arrays."""
    return "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in fields.items()) + " }"


def components_toml(*rows: str) -> str:
    body = ",\n  ".join(rows)
    return (
        "group = [\n"
        '  { id = "web", name = "Websites", note = "Checked from outside." },\n'
        "]\n\n"
        "component = [\n"
        f"  {body}\n"
        "]\n"
    )


class CommittedConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load(str(STATUSD_TOML), str(COMPONENTS_TOML))

    def test_twelve_components_in_three_groups(self):
        self.assertEqual(len(self.cfg.components), 12)
        self.assertEqual([g.id for g in self.cfg.groups], ["web", "home", "monitoring"])
        group_ids = {g.id for g in self.cfg.groups}
        self.assertEqual(len({c.id for c in self.cfg.components}), 12)
        for c in self.cfg.components:
            self.assertIn(c.group, group_ids, c.id)

    def test_files_403_is_healthy_but_the_apex_403_is_not(self):
        """files.john2143.com answers 403 normally, so 403 is in its expect.

        john2143.com deliberately omits it: a CrowdSec ban on the droplet must read
        as down, not healthy.
        """
        files = self.cfg.component("files-john2143-com")
        apex = self.cfg.component("john2143-com")
        self.assertTrue(config.expect_ok(files.expect, 403))
        self.assertFalse(config.expect_ok(apex.expect, 403))
        self.assertTrue(config.expect_ok(apex.expect, 301))


class ComponentsValidationTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        # A valid statusd.toml: every rejection below must come from the components file.
        (self.dir / "statusd.toml").write_text(STATUSD_TOML.read_text())

    def load(self, *rows: str) -> config.Config:
        (self.dir / "components.toml").write_text(components_toml(*rows))
        return config.load(str(self.dir / "statusd.toml"), str(self.dir / "components.toml"))

    def test_minimal_file_loads(self):
        cfg = self.load(row(**SITE), row(**BOX))
        self.assertEqual([c.id for c in cfg.components], ["site", "box"])
        # expect omitted means 2xx-3xx, so a redirect counts as up and a 403 does not.
        self.assertEqual(cfg.component("site").expect, ("200-399",))
        self.assertTrue(config.expect_ok(cfg.component("site").expect, 302))
        self.assertFalse(config.expect_ok(cfg.component("site").expect, 403))

    def rejects(self, fragment: str, *rows: str) -> None:
        """Assert ConfigError whose message names the defect.

        The message matters: a template that is not valid TOML would raise too, and a
        test that only checks the exception type would pass while loading nothing.
        """
        with self.assertRaises(config.ConfigError) as caught:
            self.load(*rows)
        self.assertIn(fragment, str(caught.exception))

    def test_duplicate_component_id(self):
        self.rejects("duplicate component id", row(**SITE), row(**SITE))

    def test_unknown_group(self):
        self.rejects("unknown group", row(**{**SITE, "group": "nope"}))

    def test_kind_must_be_http_or_lan(self):
        self.rejects("kind must be http or lan", row(**{**SITE, "kind": "tcp"}))

    def test_http_component_needs_a_url(self):
        without_url = {k: v for k, v in SITE.items() if k != "url"}
        self.rejects("needs url", row(**without_url))

    def test_lan_component_must_not_have_a_url(self):
        self.rejects("must not have url", row(**{**BOX, "url": "https://box.test"}))

    def test_hosted_must_be_home_or_cloud(self):
        self.rejects("hosted must be", row(**{**SITE, "hosted": "elsewhere"}))

    def test_malformed_expect_entry(self):
        self.rejects("bad expect entry", row(**{**SITE, "expect": ["200-399", "40x"]}))

    def test_expect_entry_must_be_a_full_code_or_range(self):
        for bad in ("2000", "200-", "200-3999", "2xx", "200 - 399"):
            with self.subTest(entry=bad):
                self.rejects("bad expect entry", row(**{**SITE, "expect": [bad]}))


if __name__ == "__main__":
    unittest.main()
