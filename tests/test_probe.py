"""probe_http() against a local http.server: classification, redirects, failures."""
from __future__ import annotations

import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from statusd.probe import probe_http

DEFAULT_EXPECT = ("200-399",)
SLOW_S = 3.0


class _Handler(BaseHTTPRequestHandler):
    server_version = "probefixture"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.server.hits.append(self.path)
        path = self.path.split("?", 1)[0]
        if path == "/redirect":
            self._head(301, {"Location": f"http://127.0.0.1:{self.server.server_address[1]}/target"})
        elif path == "/forbidden":
            self._head(403)
        elif path == "/boom":
            self._head(500)
        elif path == "/slow":
            time.sleep(SLOW_S)
            self._body(200)
        else:
            self._body(200)

    def _head(self, code: int, extra: dict | None = None) -> None:
        self.send_response(code)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self, code: int) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"hi")
        except OSError:  # the probe already timed out and closed the socket
            pass


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.hits = []
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def probe(self, path: str, expect=DEFAULT_EXPECT, timeout: float = 5):
        return probe_http(self.base + path, expect, timeout, "statusd-test/1.0")

    def test_classification(self):
        cases = (
            ("/ok", DEFAULT_EXPECT, True, 200),
            ("/redirect", DEFAULT_EXPECT, True, 301),  # a 3xx is a response, classified by expect
            ("/forbidden", DEFAULT_EXPECT, False, 403),
            ("/forbidden", ("200-399", "403"), True, 403),  # files.john2143.com answers 403 normally
            ("/boom", DEFAULT_EXPECT, False, 500),
        )
        for path, expect, want_ok, want_status in cases:
            with self.subTest(path=path, expect=expect):
                ok, status, latency = self.probe(path, expect)
                self.assertEqual((ok, status), (want_ok, want_status))
                if want_ok:
                    self.assertIsNotNone(latency)
                    self.assertGreaterEqual(latency, 0.0)
                else:
                    # latency is recorded only for a probe that counted as up
                    self.assertIsNone(latency)

    def test_redirect_is_never_followed(self):
        ok, status, _ = self.probe("/redirect")
        self.assertEqual((ok, status), (True, 301))
        self.assertIn("/redirect", self.server.hits)
        self.assertNotIn("/target", self.server.hits, "the Location target was requested")

    def test_refused_port(self):
        # Bind then close: nothing is listening, so the connection is refused, not slow.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertEqual(probe_http(f"http://127.0.0.1:{port}/", DEFAULT_EXPECT, 5, "ua"),
                         (False, None, None))

    def test_timeout_is_honoured(self):
        started = time.monotonic()
        result = self.probe("/slow", timeout=1)
        elapsed = time.monotonic() - started
        self.assertEqual(result, (False, None, None))
        self.assertLess(elapsed, 2.5, f"returned after {elapsed:.1f}s, not the 1 s timeout")


if __name__ == "__main__":
    unittest.main()
