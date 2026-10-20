"""Deterministic fixtures: tests/fixtures/model-<scenario>.json and designs/live-*.html.

Run from the repo root after any change under src/statusd/:
    PYTHONPATH=src python3 tests/make_fixtures.py
CI's render-drift job fails when the committed output differs.
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from statusd import config, store  # noqa: E402
from statusd.live import Live  # noqa: E402
from statusd.model import build_model, primary_vantage  # noqa: E402
from statusd.render import render_page  # noqa: E402

NOW = 1792479000  # 2026-10-20 06:50 UTC (02:50 EDT)
SCENARIOS = ("normal", "incident", "home-offline", "stale")
PREVIEWS = ("normal", "incident")
REC_S = 2700  # live samples cover the last 45 minutes
DROPLET_DAYS = 20  # droplet history for http components; older hours come from pite backfill
BACKFILL_DAYS = 29
FULL_HISTORY = {"gateway"}  # one component with a full 90-day strip

# Closed outages present in every scenario: (cid, vantage, start, duration_s).
CLOSED = (
    ("files-john2143-com", "droplet", NOW - 5 * 86400 + 3 * 3600, 2400),
    ("mail", "pite", NOW - 12 * 86400, 900),
    ("john2143-com", "pite", NOW - 25 * 86400, 3 * 3600),
    ("temporal", "pite", NOW - 2 * 86400, 300),
)


def load_config() -> config.Config:
    return config.load(str(ROOT / "config" / "statusd.toml"), str(ROOT / "config" / "components.toml"))


def _down_seconds(cid: str, vantage: str, lo: int, hi: int) -> int:
    return sum(max(0, min(s + d, hi) - max(s, lo)) for c, v, s, d in CLOSED if c == cid and v == vantage)


def _seed_history(conn: sqlite3.Connection, cfg: config.Config, rng: random.Random) -> None:
    live_from_hour = store.floor_hour(NOW - REC_S)
    live_from_bucket = store.floor_10m(NOW - REC_S)
    rows, recent = [], []
    for c in cfg.components:
        base = rng.uniform(60, 220) if c.kind == "http" else rng.uniform(1, 25)
        days = 90 if c.id in FULL_HISTORY else BACKFILL_DAYS
        first = store.floor_hour(NOW - days * 86400)
        for h in range(first, live_from_hour, 3600):
            if c.kind == "http":
                vantage = "droplet" if h >= live_from_hour - DROPLET_DAYS * 86400 else "pite"
            else:
                vantage = "pite"
            samples = 120
            ok = samples - _down_seconds(c.id, vantage, h, h + 3600) // 30
            if rng.random() < 0.01:
                ok -= rng.randint(1, 10)
            ok = max(0, ok)
            lat = round(base * rng.uniform(0.8, 1.2), 1) if ok else None
            rows.append((c.id, vantage, h, samples, ok, lat))
        pv = primary_vantage(c)
        for b in range(live_from_bucket - 48 * 3600, live_from_bucket, 600):
            ok = 20 - _down_seconds(c.id, pv, b, b + 600) // 30
            lat = round(base * rng.uniform(0.7, 1.4), 1) if ok > 0 else None
            recent.append((c.id, pv, b, 20, max(0, ok), lat))
    conn.executemany("INSERT INTO hourly VALUES (?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO recent VALUES (?,?,?,?,?,?)", recent)
    conn.executemany('INSERT INTO outage VALUES (?,?,?,?)', [(c, v, s, s + d) for c, v, s, d in CLOSED])


def _feed(live: Live, cid: str, vantage: str, end: int, fails_from: int | None, fail_at: int | None,
          base: float, rng: random.Random) -> None:
    """Record samples every 30 s from NOW-REC_S to end, failing from fails_from on (or once at fail_at)."""
    ts = NOW - REC_S
    while ts <= end:
        failing = (fails_from is not None and ts >= fails_from) or (fail_at is not None and abs(ts - fail_at) < 15)
        live.record(cid, vantage, ts, not failing, None if failing else round(base * rng.uniform(0.8, 1.2), 1))
        ts += 30


def build_scenario(name: str, db_path: str) -> tuple[sqlite3.Connection, config.Config, Live]:
    if name not in SCENARIOS:
        raise ValueError(name)
    cfg = load_config()
    rng = random.Random(2143)
    conn = store.connect(db_path)
    _seed_history(conn, cfg, rng)
    store.meta_set(conn, "last_flush", str(NOW - REC_S))
    live = Live(conn, cfg.down_after_failures)
    live.startup(NOW - REC_S)

    droplet_end = NOW - 1020 if name == "stale" else NOW - 5
    pite_end = NOW - 860 if name == "home-offline" else NOW - 12
    for c in cfg.components:
        base = 150.0 if c.kind == "http" else 8.0
        fails_from = fail_at = None
        if name == "incident" and c.id == "files-john2143-com":
            fails_from = NOW - 2400
        if name == "incident" and c.id == "john2143-com":
            fail_at = NOW - 185
        if name == "home-offline" and c.hosted == "home" and c.kind == "http":
            fails_from = NOW - 845
        if c.kind == "http":
            _feed(live, c.id, "droplet", droplet_end, fails_from, fail_at, base, rng)
            live.cert_days[c.id] = 30 + rng.randint(0, 59)
        else:
            _feed(live, c.id, "pite", pite_end, None, None, base, rng)
    live.home_last_report = pite_end
    live.flush(NOW)
    return conn, cfg, live


def main() -> None:
    out = ROOT / "tests" / "fixtures"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name in SCENARIOS:
            conn, cfg, live = build_scenario(name, f"{tmp}/{name}.db")
            model = build_model(conn, cfg, live, NOW)
            conn.close()
            (out / f"model-{name}.json").write_text(json.dumps(model, sort_keys=True, indent=1) + "\n")
            if name in PREVIEWS:
                (ROOT / "designs" / f"live-{name}.html").write_text(render_page(model, "preview"))
            print(f"{name}: {len(model['outages'])} outages, "
                  f"states {sorted({c['state'] for c in model['components']})}")


if __name__ == "__main__":
    main()
