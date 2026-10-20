"""Outside-in probes, run from the droplet for `http` components only."""
from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .config import expect_ok


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # john2143.com and 2143.me redirect to github.com; following would measure GitHub.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def probe_http(url: str, expect: tuple[str, ...], timeout: float, ua: str) -> tuple[bool, int | None, float | None]:
    """GET url without following redirects. Returns (ok, status, latency_ms)."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": ua})
    t0 = time.monotonic()
    try:
        resp = _OPENER.open(req, timeout=timeout)
        status = resp.status
        latency = (time.monotonic() - t0) * 1000
        resp.close()
    except urllib.error.HTTPError as e:  # a response (3xx/4xx/5xx), classified against expect
        status = e.code
        latency = (time.monotonic() - t0) * 1000
        e.close()
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, ConnectionError, OSError):
        return False, None, None
    ok = expect_ok(expect, status)
    return ok, status, (latency if ok else None)


def cert_days(host: str, timeout: float = 10) -> int | None:
    """Whole days until the TLS certificate of host:443 expires; None on any error."""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        expires = ssl.cert_time_to_seconds(cert["notAfter"])
        return int((expires - time.time()) // 86400)
    except (OSError, ssl.SSLError, KeyError, ValueError, TypeError):
        return None


def host_of(url: str) -> str:
    return urlsplit(url).hostname or ""
