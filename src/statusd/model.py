"""build_model(): the single source of status.json and the page (schema `v: 1`).

Key names and order follow designs/sample-*.json; nothing else is added.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Component, Config
from .live import STATE_LOCK, Live
from .store import floor_10m, floor_hour

DAYS = 90
HOURS = 168
LAT_BUCKETS = 144
MERGE_GAP_S = 120
DEGRADED_WINDOW_S = 600
UPTIME_WINDOWS = (("24h", 24), ("7d", 168), ("30d", 720), ("90d", 2160))


def primary_vantage(c: Component) -> str:
    return "droplet" if c.kind == "http" else "pite"


def local_midnight(tz: ZoneInfo, d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=tz).timestamp())


def day_bounds(tz: ZoneInfo, d: date) -> tuple[int, int]:
    """[local midnight of d, local midnight of d+1) as unix seconds; 23 or 25 h on DST days."""
    return local_midnight(tz, d), local_midnight(tz, d + timedelta(days=1))


def _pct(ok: int, samples: int, nd: int) -> float | None:
    return round(ok * 100 / samples, nd) if samples else None


def _hourly_series(conn: sqlite3.Connection, c: Component) -> dict[int, tuple[int, int]]:
    """hour -> (samples, ok): primary vantage, plus pite backfill for droplet-less hours (http only)."""
    pv = primary_vantage(c)
    series = {h: (s, o) for h, s, o in conn.execute(
        "SELECT hour, samples, ok FROM hourly WHERE cid=? AND vantage=?", (c.id, pv))}
    if c.kind == "http":
        for h, s, o in conn.execute(
                "SELECT hour, samples, ok FROM hourly WHERE cid=? AND vantage='pite'", (c.id,)):
            series.setdefault(h, (s, o))
    return series


def _merged_outages(conn: sqlite3.Connection, c: Component, now: int) -> list[tuple[int, int | None]]:
    pv = primary_vantage(c)
    rows = list(conn.execute('SELECT start, "end" FROM outage WHERE cid=? AND vantage=?', (c.id, pv)))
    if c.kind == "http":
        first = conn.execute(
            "SELECT MIN(hour) FROM hourly WHERE cid=? AND vantage='droplet'", (c.id,)).fetchone()[0]
        rows += [(s, e) for s, e in conn.execute(
            'SELECT start, "end" FROM outage WHERE cid=? AND vantage=\'pite\'', (c.id,))
            if first is None or s < first]
    merged: list[list] = []
    for s, e in sorted(rows, key=lambda r: r[0]):
        if merged:
            ps, pe = merged[-1]
            if pe is None or s <= pe + MERGE_GAP_S:
                merged[-1][1] = None if (pe is None or e is None) else max(pe, e)
                continue
        merged.append([s, e])
    return [(s, e) for s, e in merged if e is None or e >= now - DAYS * 86400]


def build_model(conn: sqlite3.Connection, cfg: Config, live: Live, now: int) -> dict:
    with STATE_LOCK:
        return _build(conn, cfg, live, now)


def _build(conn: sqlite3.Connection, cfg: Config, live: Live, now: int) -> dict:
    tz = ZoneInfo(cfg.tz)
    today = datetime.fromtimestamp(now, tz).date()
    first_day = today - timedelta(days=DAYS - 1)
    day_spans = [day_bounds(tz, first_day + timedelta(days=i)) for i in range(DAYS)]
    hours_start = floor_hour(now) - (HOURS - 1) * 3600
    latency_start = floor_10m(now) - (LAT_BUCKETS - 1) * 600
    window_end = floor_hour(now) + 3600

    components = []
    all_outages = []
    for c in cfg.components:
        pv = primary_vantage(c)
        t = live.tracks.get((c.id, pv))
        fresh = t is not None and t.last_ts is not None and now - t.last_ts <= cfg.stale_seconds
        if not fresh:
            state = "unknown"
        elif t.open_start is not None:
            state = "down"
        elif any(not ok for ts, ok in t.window if ts >= now - DEGRADED_WINDOW_S):
            state = "degraded"
        else:
            state = "up"
        latency = round(t.last_latency, 1) if fresh and t.last_ok and t.last_latency is not None else None

        series = _hourly_series(conn, c)
        uptime = {}
        for key, n in UPTIME_WINDOWS:
            lo = window_end - n * 3600
            s = o = 0
            for h, (hs, ho) in series.items():
                if lo <= h < window_end:
                    s += hs
                    o += ho
            uptime[key] = _pct(o, s, 4)

        outages = _merged_outages(conn, c, now)
        daily_pct, daily_down = [], []
        for lo, hi in day_spans:
            s = o = 0
            for h, (hs, ho) in series.items():
                if lo <= h < hi:
                    s += hs
                    o += ho
            p = _pct(o, s, 3)
            daily_pct.append(p)
            if p is None:
                daily_down.append(None)
            else:
                secs = sum(max(0, min(now if e is None else e, hi) - max(st, lo)) for st, e in outages)
                daily_down.append(round(secs / 60))

        hourly_pct = []
        for i in range(HOURS):
            row = series.get(hours_start + i * 3600)
            hourly_pct.append(_pct(row[1], row[0], 3) if row else None)

        lat = {b: l for b, l in conn.execute(
            "SELECT bucket, latency_ms FROM recent WHERE cid=? AND vantage=? AND bucket>=?",
            (c.id, pv, latency_start))}
        latency_10m = []
        for i in range(LAT_BUCKETS):
            v = lat.get(latency_start + i * 600)
            latency_10m.append(round(v, 1) if v is not None else None)

        comp = {
            "id": c.id,
            "name": c.name,
            "group": c.group,
            "hosted": c.hosted,
            "state": state,
            "latency_ms": latency,
            "cert_days": live.cert_days.get(c.id) if c.kind == "http" else None,
            "uptime": uptime,
            "since": min(series) if series else None,
            "daily_pct": daily_pct,
            "daily_down_min": daily_down,
            "hourly_pct": hourly_pct,
            "latency_10m": latency_10m,
        }
        if c.kind == "http":
            comp["url"] = c.url
        components.append(comp)
        all_outages += [{"id": c.id, "start": s, "end": e} for s, e in outages]

    all_outages.sort(key=lambda o: (-o["start"], o["id"]))
    droplet_ts = [t.last_ts for (cid, v), t in live.tracks.items()
                  if v == "droplet" and t.last_ts is not None]
    return {
        "v": 1,
        "generated": now,
        "tz": cfg.tz,
        "probe_interval_s": cfg.interval_seconds,
        "home_age_s": now - live.home_last_report if live.home_last_report is not None else None,
        "groups": [{"id": g.id, "name": g.name, "note": g.note} for g in cfg.groups],
        "days_start": first_day.isoformat(),
        "hours_start": hours_start,
        "latency_start": latency_start,
        "components": components,
        "outages": all_outages,
        "sample_age_s": now - max(droplet_ts) if droplet_ts else None,
    }
