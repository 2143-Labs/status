"""python3 -m statusd --config ... --components ... [--build-sha SHA]"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, probe, store
from .feed import render_feed
from .live import Live
from .model import build_model
from .render import render_page
from .server import App, make_server

log = logging.getLogger("statusd")
FLUSH_S = 60
RENDER_S = 30
TLS_S = 3600


def every(interval: float, fn, stop: threading.Event, name: str) -> None:
    """Run fn now and then every interval seconds on a monotonic schedule; never stacks."""
    nxt = time.monotonic()
    while not stop.is_set():
        try:
            fn()
        except Exception:
            log.exception("%s failed", name)
        nxt += interval
        if nxt < time.monotonic():  # overran: skip the missed ticks
            nxt = time.monotonic() + interval
        stop.wait(max(0.0, nxt - time.monotonic()))


def write_atomic(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="statusd")
    ap.add_argument("--config", required=True)
    ap.add_argument("--components", required=True)
    ap.add_argument("--build-sha", default=os.environ.get("STATUS_BUILD_SHA", "unknown"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    try:
        cfg = config.load(args.config, args.components)
    except config.ConfigError as e:
        log.error("config: %s", e)
        return 2

    conn = store.connect(cfg.db)
    live = Live(conn, cfg.down_after_failures)
    live.startup(int(time.time()))
    app = App(cfg=cfg, live=live, build_sha=args.build_sha, started=time.monotonic())
    server = make_server(app)
    stop = threading.Event()
    http_comps = [c for c in cfg.components if c.kind == "http"]
    pool = ThreadPoolExecutor(max_workers=8)

    def probe_once() -> None:
        futures = {c.id: pool.submit(probe.probe_http, c.url, c.expect, cfg.timeout_seconds, cfg.user_agent)
                   for c in http_comps}
        for cid, fut in futures.items():
            try:
                ok, _status, latency = fut.result()
            except Exception:
                log.exception("probe %s raised", cid)
                ok, latency = False, None
            live.record(cid, "droplet", int(time.time()), ok, latency)

    def tls_once() -> None:
        for c in http_comps:
            days = probe.cert_days(probe.host_of(c.url), cfg.timeout_seconds)
            if days is not None:
                live.set_cert_days(c.id, days)

    def render_once() -> None:
        m = build_model(conn, cfg, live, int(time.time()))
        write_atomic(os.path.join(cfg.www, "status.json"), json.dumps(m, separators=(",", ":")))
        write_atomic(os.path.join(cfg.www, "index.html"), render_page(m, args.build_sha))
        write_atomic(os.path.join(cfg.www, "feed.xml"), render_feed(m))

    def flush_once() -> None:
        live.flush(int(time.time()))

    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    loops = []
    for name, interval, fn in (("probe", cfg.interval_seconds, probe_once), ("flush", FLUSH_S, flush_once),
                               ("render", RENDER_S, render_once), ("tls", TLS_S, tls_once)):
        t = threading.Thread(target=every, args=(interval, fn, stop, name), name=name, daemon=True)
        t.start()
        loops.append(t)

    # The handler only sets the event; shutdown() must not run on the serving thread.
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("statusd %s listening on %s:%d", args.build_sha, cfg.listen_host, cfg.listen_port)
    while not stop.wait(1):
        pass

    server.shutdown()
    deadline = time.monotonic() + 12  # a probe round is bounded by the 10 s timeout; TimeoutStopSec=20
    for t in loops:
        t.join(max(0.0, deadline - time.monotonic()))
    pool.shutdown(wait=False, cancel_futures=True)
    live.shutdown(int(time.time()))
    conn.close()
    log.info("statusd stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
