"""ingest + server: auth, body cap, versioning, report/backfill semantics, on a real server."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from statusd import config, ingest, server, store
from statusd.live import Live

ROOT = Path(__file__).resolve().parent.parent
STATUSD_TOML = ROOT / "config" / "statusd.toml"
COMPONENTS_TOML = ROOT / "config" / "components.toml"

NOW = 1792479000
TOKEN = "test-token"
TOKEN_SHA = hashlib.sha256(TOKEN.encode()).hexdigest()


class _Case(unittest.TestCase):
    """A temp config, a temp SQLite DB and a Live on it. No server."""
    token_sha = TOKEN_SHA

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "www").mkdir()
        (self.dir / "snapshots").mkdir()
        self.cfg = replace(
            config.load(str(STATUSD_TOML), str(COMPONENTS_TOML)),
            listen_host="127.0.0.1", listen_port=0, token_sha256=self.token_sha,
            db=str(self.dir / "history.db"), www=str(self.dir / "www"),
            snapshots=str(self.dir / "snapshots"), deploy_failed=str(self.dir / "deploy-failed"),
        )
        self.conn = store.connect(self.cfg.db)
        self.addCleanup(self.conn.close)
        self.live = Live(self.conn, self.cfg.down_after_failures)

    def rows(self):
        hourly = [tuple(r) for r in self.conn.execute(
            "SELECT cid, vantage, hour, samples, ok, latency_ms FROM hourly ORDER BY cid, vantage, hour")]
        outages = [tuple(r) for r in self.conn.execute(
            'SELECT cid, vantage, start, "end" FROM outage ORDER BY cid, vantage, start')]
        return hourly, outages


class ServerCase(_Case):
    """Adds a real ThreadingHTTPServer bound to 127.0.0.1:0."""

    def setUp(self):
        super().setUp()
        self.srv = server.make_server(server.App(self.cfg, self.live, "deadbeef", time.monotonic()))
        self.port = self.srv.server_address[1]
        thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def request(self, path: str, body: bytes = b"", token: str | None = TOKEN,
                content_length: int | None = None, method: str = "POST"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(conn.close)
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
        return resp.status, payload


class AuthTest(ServerCase):
    def test_every_v1_path_needs_a_bearer_token(self):
        cases = (
            ("POST", "/v1/report", None),                 # no Authorization header at all
            ("POST", "/v1/report", "wrong-token"),
            ("POST", "/v1/backfill", "wrong-token"),
            ("GET", "/v1/snapshot", None),
        )
        for method, path, token in cases:
            with self.subTest(path=path, token=token):
                status, _ = self.request(path, body=b"{}", token=token, method=method)
                self.assertEqual(status, 401)

    def test_healthz_stays_open_without_a_token(self):
        # The deploy's own health check reads this, so a token here would break every rollout.
        status, body = self.request("/healthz", token=None, method="GET")
        self.assertEqual(status, 200)
        self.assertIs(body["ok"], True)


class EmptyTokenTest(ServerCase):
    token_sha = ""

    def test_an_unconfigured_token_makes_every_request_unauthorized(self):
        status, _ = self.request("/v1/report", body=b'{"v":1,"checks":[]}', token="")
        self.assertEqual(status, 401)
        status, _ = self.request("/v1/report", body=b'{"v":1,"checks":[]}', token=TOKEN)
        self.assertEqual(status, 401)


class BodyTest(ServerCase):
    def test_65_kib_is_parsed_and_one_byte_more_is_refused(self):
        # Exactly MAX_BODY reaches the JSON parser, so invalid JSON there is a 400, not a 413.
        status, _ = self.request("/v1/report", body=b"x" * ingest.MAX_BODY)
        self.assertEqual(status, 400)
        # One byte over: refused on Content-Length alone, before the body is read.
        status, _ = self.request("/v1/report", body=b"", content_length=ingest.MAX_BODY + 1)
        self.assertEqual(status, 413)

    def test_invalid_json_is_a_bad_request(self):
        status, _ = self.request("/v1/report", body=b"not json")
        self.assertEqual(status, 400)

    def test_wrong_version_is_a_bad_request(self):
        for body in (b'{"v":2,"checks":[]}', b"{}", b'[{"v":1}]', b'{"v":"1","checks":[]}'):
            with self.subTest(body=body):
                status, _ = self.request("/v1/report", body=body)
                self.assertEqual(status, 400)


class ReportTest(ServerCase):
    def test_a_report_accepts_lan_ids_and_ignores_an_http_id(self):
        payload = {"v": 1, "ts": 0, "checks": [
            {"id": "gateway", "ok": True, "latency_ms": 2.1},
            {"id": "john2143-com", "ok": True, "latency_ms": 30.0},
        ]}
        status, body = self.request("/v1/report", body=json.dumps(payload).encode())
        self.assertEqual((status, body), (200, {"accepted": 1, "ignored": 1}))
        self.assertIn(("gateway", "pite"), self.live.tracks)
        self.assertNotIn(("john2143-com", "pite"), self.live.tracks)
        self.assertIsNotNone(self.live.home_last_report)


class HomeReportTest(_Case):
    def test_an_empty_report_leaves_home_last_report_alone(self):
        self.live.home_last_report = NOW - 5
        result = ingest.report(self.cfg, self.live, {"v": 1, "checks": []}, NOW)
        self.assertEqual(result, {"accepted": 0, "ignored": 0})
        self.assertEqual(self.live.home_last_report, NOW - 5)

    def test_a_report_with_nothing_accepted_leaves_home_last_report_alone(self):
        result = ingest.report(self.cfg, self.live,
                               {"v": 1, "checks": [{"id": "john2143-com", "ok": True}]}, NOW)
        self.assertEqual(result, {"accepted": 0, "ignored": 1})
        self.assertIsNone(self.live.home_last_report)

    def test_an_unknown_check_id_is_logged_once_not_every_30_s(self):
        # An id no test configures, so the module-level "already logged" set is untouched.
        payload = {"v": 1, "checks": [{"id": "never-configured", "ok": True}]}
        with self.assertLogs("statusd.ingest", level="WARNING") as logged:
            self.assertEqual(ingest.report(self.cfg, self.live, payload, NOW),
                             {"accepted": 0, "ignored": 1})
        self.assertEqual(len(logged.records), 1)
        with self.assertNoLogs("statusd.ingest", level="WARNING"):
            ingest.report(self.cfg, self.live, payload, NOW)


class BackfillTest(_Case):
    HOUR = store.floor_hour(NOW) - 5 * 3600

    def payload(self, hour: int) -> dict:
        return {"v": 1,
                "hours": [{"id": "john2143-com", "hour": hour, "samples": 120, "ok": 118,
                           "latency_ms": 150.0}],
                "outages": [{"id": "john2143-com", "start": NOW - 7200, "end": NOW - 3600}]}

    def test_running_the_same_backfill_twice_changes_nothing(self):
        first = ingest.backfill(self.cfg, self.live, self.payload(self.HOUR), NOW)
        after_first = self.rows()
        second = ingest.backfill(self.cfg, self.live, self.payload(self.HOUR), NOW)
        self.assertEqual(first, {"accepted": 2, "rejected": 0})
        self.assertEqual(second, first)
        self.assertEqual(self.rows(), after_first)
        self.assertEqual(len(after_first[0]), 1)

    def test_backfill_keeps_the_live_row_for_the_same_hour(self):
        # A sample still sitting in the accumulator must not be summed into the backfilled row.
        self.live.record("john2143-com", "droplet", self.HOUR + 60, True, 12.0)
        ingest.backfill(self.cfg, self.live, self.payload(self.HOUR), NOW)
        live_row = self.conn.execute(
            "SELECT samples, ok, latency_ms FROM hourly WHERE cid='john2143-com' AND vantage='droplet'"
            " AND hour=?", (self.HOUR,)).fetchone()
        backfill_row = self.conn.execute(
            "SELECT samples, ok, latency_ms FROM hourly WHERE cid='john2143-com' AND vantage='pite'"
            " AND hour=?", (self.HOUR,)).fetchone()
        self.assertEqual(live_row, (1, 1, 12.0))
        self.assertEqual(backfill_row, (120, 118, 150.0))

    def test_the_current_hour_is_rejected(self):
        current = store.floor_hour(NOW)
        payload = {"v": 1, "hours": [
            {"id": "john2143-com", "hour": current, "samples": 10, "ok": 10},
            {"id": "john2143-com", "hour": current - 3600, "samples": 10, "ok": 10},
        ]}
        self.assertEqual(ingest.backfill(self.cfg, self.live, payload, NOW),
                         {"accepted": 1, "rejected": 1})
        hours = [r[2] for r in self.rows()[0]]
        self.assertEqual(hours, [current - 3600])

    def test_an_unknown_id_is_rejected(self):
        payload = {"v": 1, "hours": [{"id": "nope", "hour": self.HOUR, "samples": 10, "ok": 10}]}
        self.assertEqual(ingest.backfill(self.cfg, self.live, payload, NOW),
                         {"accepted": 0, "rejected": 1})
        self.assertEqual(self.rows(), ([], []))


if __name__ == "__main__":
    unittest.main()
