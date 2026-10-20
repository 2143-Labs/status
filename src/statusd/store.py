"""SQLite history: hourly rows kept forever, 10-minute rows for 48 h, outages, meta."""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS hourly(cid TEXT NOT NULL, vantage TEXT NOT NULL, hour INTEGER NOT NULL,
  samples INTEGER NOT NULL, ok INTEGER NOT NULL, latency_ms REAL, PRIMARY KEY (cid, vantage, hour));
CREATE TABLE IF NOT EXISTS recent(cid TEXT NOT NULL, vantage TEXT NOT NULL, bucket INTEGER NOT NULL,
  samples INTEGER NOT NULL, ok INTEGER NOT NULL, latency_ms REAL, PRIMARY KEY (cid, vantage, bucket));
CREATE TABLE IF NOT EXISTS outage(cid TEXT NOT NULL, vantage TEXT NOT NULL, start INTEGER NOT NULL,
  "end" INTEGER, PRIMARY KEY (cid, vantage, start));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

RECENT_KEEP_S = 172800

# SQLite evaluates every SET expression against the old row, so `ok` below is the stored count.
_UPSERT = """
INSERT INTO {table} VALUES (?,?,?,?,?,?) ON CONFLICT(cid,vantage,{slot}) DO UPDATE SET
  samples = samples + excluded.samples,
  ok = ok + excluded.ok,
  latency_ms = CASE WHEN ok + excluded.ok = 0 THEN NULL
    ELSE (COALESCE(latency_ms,0)*ok + COALESCE(excluded.latency_ms,0)*excluded.ok) / (ok + excluded.ok) END
"""

# (table, cid, vantage, slot) -> [samples, ok, latency_sum, latency_count]
Accumulator = dict[tuple[str, str, str, int], list]


def floor_hour(ts: int) -> int:
    return ts - ts % 3600


def floor_10m(ts: int) -> int:
    return ts - ts % 600


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    return conn


def add(acc: Accumulator, cid: str, vantage: str, ts: int, ok: bool, latency_ms: float | None) -> None:
    for table, slot in (("hourly", floor_hour(ts)), ("recent", floor_10m(ts))):
        e = acc.setdefault((table, cid, vantage, slot), [0, 0, 0.0, 0])
        e[0] += 1
        if ok:
            e[1] += 1
            if latency_ms is not None:
                e[2] += latency_ms
                e[3] += 1


def flush(conn: sqlite3.Connection, acc: Accumulator, now: int) -> None:
    """Upsert every accumulated slot in one transaction, prune `recent`, stamp last_flush."""
    conn.execute("BEGIN")
    try:
        for (table, cid, vantage, slot), (samples, ok, lat_sum, lat_n) in acc.items():
            slot_col = "hour" if table == "hourly" else "bucket"
            mean = lat_sum / lat_n if lat_n else None
            conn.execute(_UPSERT.format(table=table, slot=slot_col), (cid, vantage, slot, samples, ok, mean))
        conn.execute("DELETE FROM recent WHERE bucket < ?", (now - RECENT_KEEP_S,))
        meta_set(conn, "last_flush", str(now))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    acc.clear()


def meta_get(conn: sqlite3.Connection, k: str) -> str | None:
    row = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else None


def meta_set(conn: sqlite3.Connection, k: str, v: str) -> None:
    conn.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def close_dangling(conn: sqlite3.Connection, now: int) -> None:
    """At startup: close outages left open by a crash at the last flush (never before start)."""
    lf = meta_get(conn, "last_flush")
    t = int(lf) if lf is not None else now
    conn.execute('UPDATE outage SET "end" = MAX(start, ?) WHERE "end" IS NULL', (t,))
