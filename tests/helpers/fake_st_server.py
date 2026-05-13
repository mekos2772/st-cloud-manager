"""Fake SillyTavern HTTP server — minimal mock for HTTP E2E testing.

Starts on a given port and returns plausible responses so the
manager's path_proxy can successfully route requests.

Endpoints:
    GET  /              → 200 HTML page
    GET  /css/*         → 200 CSS
    GET  /scripts/*     → 200 JS
    GET  /socket.io/*   → 200 (empty)
    POST /api/*         → 200 JSON
    GET  /api/*         → 200 JSON
    *   /*              → 200 (catch-all)
"""
from __future__ import annotations

import json
import os
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

FAKE_HTML = "<html><head><link rel='stylesheet' href='/css/app.css'></head><body><div id='app'>ST</div><script src='/scripts/app.js'></script></body></html>"
FAKE_CSS = "body { background: #000; color: #fff; }"
FAKE_JS = "console.log('ST loaded');"
FAKE_JSON = json.dumps({"status": "ok", "version": "1.12.0"}).encode()


class FakeSTHandler(BaseHTTPRequestHandler):

    def _ok(self, content_type: str, body: str | bytes):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._ok("text/html", FAKE_HTML)
        elif path.startswith("/css/"):
            self._ok("text/css", FAKE_CSS)
        elif path.startswith("/scripts/"):
            self._ok("application/javascript", FAKE_JS)
        elif path.startswith("/socket.io/"):
            self._ok("text/plain", "")
        elif path.startswith("/api/"):
            self._ok("application/json", FAKE_JSON)
        else:
            self._ok("text/plain", f"FakeST: {path}")

    def do_POST(self):
        self._ok("application/json", FAKE_JSON)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # silence logs


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeSTServer:
    """Runs a fake SillyTavern server on a random port in a daemon thread."""

    def __init__(self):
        self.port = _find_free_port()
        self._server = HTTPServer(("127.0.0.1", self.port), FakeSTHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._server.shutdown()

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"
