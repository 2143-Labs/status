"""Token check and the two write endpoints' logic: /v1/report and /v1/backfill."""
from __future__ import annotations

import hashlib
import hmac
import logging

from .config import Config
from .live import STATE_LOCK, Live
from .store import floor_hour

log = logging.getLogger("statusd.ingest")
MAX_BODY = 65536
MAX_SAMPLES_PER_HOUR = 720
_logged_ignored: set[str] = set()


class BadRequest(ValueError):
    pass


def authorized(cfg: Config, header: str | None) -> bool:
    if not cfg.token_sha256 or not header or not header.startswith("Bearer "):
        return False
    digest = hashlib.sha256(header[len("Bearer "):].encode()).hexdigest()
    return hmac.compare_digest(digest, cfg.token_sha256)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num_or_none(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))


def check_version(payload) -> dict:
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise BadRequest("want a JSON object with v = 1")
    return payload


def report(cfg: Config, live: Live, payload: dict, now: int) -> dict:
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise BadRequest("checks must be a list")
    lan = {c.id for c in cfg.components if c.kind == "lan"}
    accepted = ignored = 0
    for ch in checks:
        cid = ch.get("id") if isinstance(ch, dict) else None
        if (cid in lan and isinstance(ch.get("ok"), bool) and _num_or_none(ch.get("latency_ms"))):
            live.record(cid, "pite", now, ch["ok"], ch.get("latency_ms"))
            accepted += 1
        else:
            ignored += 1
            key = repr(cid)
            if key not in _logged_ignored:
                _logged_ignored.add(key)
                log.warning("report: ignoring check id %s (not a configured lan component, or malformed)", key)
    if accepted:
        with STATE_LOCK:
            live.home_last_report = now
    return {"accepted": accepted, "ignored": ignored}


def backfill(cfg: Config, live: Live, payload: dict, now: int) -> dict:
    hours = payload.get("hours", [])
    outages = payload.get("outages", [])
    if not isinstance(hours, list) or not isinstance(outages, list):
        raise BadRequest("hours and outages must be lists")
    ids = {c.id for c in cfg.components}
    current_hour = floor_hour(now)
    accepted = rejected = 0
    with STATE_LOCK:
        # Write pending live samples first, so a backfilled row can never be summed into later.
        live.flush(now)
        conn = live.conn
        conn.execute("BEGIN")
        try:
            for r in hours:
                ok = (isinstance(r, dict) and r.get("id") in ids
                      and all(_int(r.get(k)) for k in ("hour", "samples", "ok"))
                      and r["hour"] % 3600 == 0 and r["hour"] < current_hour
                      and 0 <= r["ok"] <= r["samples"] <= MAX_SAMPLES_PER_HOUR
                      and _num_or_none(r.get("latency_ms")))
                if ok:
                    conn.execute("INSERT OR IGNORE INTO hourly VALUES (?, 'pite', ?, ?, ?, ?)",
                                 (r["id"], r["hour"], r["samples"], r["ok"], r.get("latency_ms")))
                    accepted += 1
                else:
                    rejected += 1
            for o in outages:
                ok = (isinstance(o, dict) and o.get("id") in ids
                      and _int(o.get("start")) and _int(o.get("end"))
                      and o["start"] < o["end"] <= now)
                if ok:
                    conn.execute("INSERT OR IGNORE INTO outage VALUES (?, 'pite', ?, ?)",
                                 (o["id"], o["start"], o["end"]))
                    accepted += 1
                else:
                    rejected += 1
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return {"accepted": accepted, "rejected": rejected}
