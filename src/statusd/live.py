"""In-memory live state, outage bookkeeping and the one lock around it and the DB.

Every DB access and every record() runs under STATE_LOCK. Load is about one call
per second, so a single lock is enough and cannot deadlock.
"""
from __future__ import annotations

import sqlite3
import threading
from collections import deque
from dataclasses import dataclass, field

from . import store

STATE_LOCK = threading.RLock()


@dataclass
class Track:
    """Live state of one (component, vantage)."""
    last_ts: int | None = None
    last_ok: bool | None = None
    last_latency: float | None = None
    window: deque = field(default_factory=lambda: deque(maxlen=40))  # (ts, ok)
    fail_streak: int = 0
    streak_start: int | None = None
    open_start: int | None = None


class Live:
    def __init__(self, conn: sqlite3.Connection, down_after_failures: int = 2):
        self.conn = conn
        self.down_after = down_after_failures
        self.tracks: dict[tuple[str, str], Track] = {}
        self.cert_days: dict[str, int] = {}
        self.home_last_report: int | None = None
        self.acc: store.Accumulator = {}

    def track(self, cid: str, vantage: str) -> Track:
        return self.tracks.setdefault((cid, vantage), Track())

    def startup(self, now: int) -> None:
        with STATE_LOCK:
            store.close_dangling(self.conn, now)
            for k, v in self.conn.execute("SELECT k, v FROM meta WHERE k LIKE 'cert:%'"):
                try:
                    self.cert_days[k[5:]] = int(v)
                except ValueError:
                    pass

    def record(self, cid: str, vantage: str, ts: int, ok: bool, latency_ms: float | None) -> None:
        with STATE_LOCK:
            store.add(self.acc, cid, vantage, ts, ok, latency_ms)
            t = self.track(cid, vantage)
            t.last_ts, t.last_ok = ts, ok
            t.last_latency = latency_ms if ok else None
            t.window.append((ts, ok))
            if ok:
                if t.open_start is not None:
                    self.conn.execute(
                        'UPDATE outage SET "end"=? WHERE cid=? AND vantage=? AND start=?',
                        (ts, cid, vantage, t.open_start),
                    )
                    t.open_start = None
                t.fail_streak = 0
                t.streak_start = None
            else:
                if t.fail_streak == 0:
                    t.streak_start = ts
                t.fail_streak += 1
                if t.fail_streak == self.down_after and t.open_start is None:
                    self.conn.execute(
                        'INSERT OR IGNORE INTO outage VALUES (?,?,?,NULL)', (cid, vantage, t.streak_start)
                    )
                    t.open_start = t.streak_start

    def set_cert_days(self, cid: str, days: int) -> None:
        with STATE_LOCK:
            self.cert_days[cid] = days
            store.meta_set(self.conn, f"cert:{cid}", str(days))

    def flush(self, now: int) -> None:
        with STATE_LOCK:
            store.flush(self.conn, self.acc, now)

    def shutdown(self, now: int) -> None:
        with STATE_LOCK:
            store.flush(self.conn, self.acc, now)
            self.conn.execute('UPDATE outage SET "end"=MAX(start, ?) WHERE "end" IS NULL', (now,))
            for t in self.tracks.values():
                t.open_start = None
