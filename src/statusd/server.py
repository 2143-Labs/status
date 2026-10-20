"""The HTTP endpoints behind Caddy: /v1/*, /healthz and /home-status."""
from __future__ import annotations

import glob
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ingest
from .config import Config
from .live import STATE_LOCK, Live

log = logging.getLogger("statusd.server")
HOME_SILENT_S = 900
HEALTH_GRACE_S = 60
SAMPLE_STALE_S = 120
CHUNK = 64 * 1024


@dataclass
class App:
    cfg: Config
    live: Live
    build_sha: str
    started: float  # time.monotonic() at start


def _deploy_state(path: str) -> tuple[str, str | None]:
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return "ok", None
    except OSError as e:
        return "failed", f"unreadable deploy-failed file: {e}"
    return "failed", (lines[1] if len(lines) > 1 else "unknown")


def health(app: App, now: int) -> tuple[int, dict]:
    with STATE_LOCK:
        droplet = [t.last_ts for (cid, v), t in app.live.tracks.items() if v == "droplet" and t.last_ts is not None]
        home = app.live.home_last_report
    sample_age = now - max(droplet) if droplet else None
    uptime = int(time.monotonic() - app.started)
    last_deploy, deploy_error = _deploy_state(app.cfg.deploy_failed)
    try:
        db_bytes = os.path.getsize(app.cfg.db)
    except OSError:
        db_bytes = 0
    ok = not (uptime > HEALTH_GRACE_S and (sample_age is None or sample_age > SAMPLE_STALE_S))
    return (200 if ok else 503), {
        "ok": ok,
        "build_sha": app.build_sha,
        "uptime_s": uptime,
        "db_bytes": db_bytes,
        "sample_age_s": sample_age,
        "home_age_s": now - home if home is not None else None,
        "last_deploy": last_deploy,
        "deploy_error": deploy_error,
    }


def home_status(app: App, now: int) -> tuple[int, dict]:
    with STATE_LOCK:
        home = app.live.home_last_report
    age = now - home if home is not None else None
    return (200 if age is not None and age <= HOME_SILENT_S else 503), {"home_age_s": age}


def make_server(app: App) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "statusd"
        sys_version = ""

        def log_message(self, fmt, *args):  # errors only (log_error writes below)
            pass

        def log_error(self, fmt, *args):
            sys.stderr.write("statusd http: " + (fmt % args) + "\n")

        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _auth(self) -> bool:
            if ingest.authorized(app.cfg, self.headers.get("Authorization")):
                return True
            self._json(401, {"error": "unauthorized"})
            return False

        def do_GET(self):
            now = int(time.time())
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._json(*health(app, now))
            elif path == "/home-status":
                self._json(*home_status(app, now))
            elif path.startswith("/v1/"):
                if not self._auth():
                    return
                if path == "/v1/snapshot":
                    self._snapshot()
                else:
                    self._json(404, {"error": "not found"})
            else:
                self._json(404, {"error": "not found"})

        def _snapshot(self) -> None:
            snaps = sorted(glob.glob(os.path.join(app.cfg.snapshots, "history-*.db")))
            if not snaps:
                self._json(404, {"error": "no snapshot yet"})
                return
            path = snaps[-1]
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
            self.end_headers()
            with open(path, "rb") as f:
                while chunk := f.read(CHUNK):
                    self.wfile.write(chunk)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if not path.startswith("/v1/"):
                self._json(404, {"error": "not found"})
                return
            if not self._auth():
                return
            handler = {"/v1/report": ingest.report, "/v1/backfill": ingest.backfill}.get(path)
            if handler is None:
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._json(400, {"error": "bad Content-Length"})
                return
            if length > ingest.MAX_BODY:
                self.close_connection = True
                self._json(413, {"error": "body too large"})
                return
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                payload = ingest.check_version(json.loads(raw))
                result = handler(app.cfg, app.live, payload, int(time.time()))
            except (ValueError, UnicodeDecodeError) as e:  # json.JSONDecodeError and BadRequest are ValueErrors
                self._json(400, {"error": str(e)[:200]})
                return
            self._json(200, result)

    srv = ThreadingHTTPServer((app.cfg.listen_host, app.cfg.listen_port), Handler)
    srv.daemon_threads = True
    return srv
