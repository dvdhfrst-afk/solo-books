"""The local server: who may ask, which handler answers, and how a failure is answered. It listens on 127.0.0.1 only."""
from __future__ import annotations

import os
import secrets
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from ..db import connect, friendly_error
from ..errors import BooksError
from . import routes

MAX_BODY_BYTES = 8_000_000
LOCAL_ADDRESS = "127.0.0.1"
# One stalled connection must not hold a thread forever.
REQUEST_TIMEOUT_SECONDS = 30


class ReviewHandler(BaseHTTPRequestHandler):
    server: "ReviewServer"
    server_version = "solo-books"
    sys_version = ""
    timeout = REQUEST_TIMEOUT_SECONDS

    def log_message(self, format: str, *args) -> None:
        if self.server.verbose:
            super().log_message(format, *args)

    def _host_allowed(self) -> bool:
        # A hostile web page can point its own name at this address, and the browser would then
        # treat this server as that page's own origin. Requiring a local Host header stops that.
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"{LOCAL_ADDRESS}:{port}", f"localhost:{port}")

    def _send(self, status: int, payload: bytes, content_type: str, extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data: dict) -> None:
        self._send(status, routes.to_json(data), "application/json")

    def _token_ok(self, offered: str) -> bool:
        return secrets.compare_digest(offered.encode("utf-8", "replace"), self.server.token.encode("utf-8"))

    def _header_token_ok(self) -> bool:
        # Another site can make a browser send a request here, but it cannot read the page to learn this token.
        if self._token_ok(self.headers.get("X-Solo-Books-Token", "")):
            return True
        self._json(403, {"error": "missing or wrong page token; reload the page"})
        return False

    def _send_page(self, render: Callable[[str, str], str]) -> None:
        nonce = secrets.token_urlsafe(16)
        page = render(self.server.token, nonce)
        policy = (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", {"Content-Security-Policy": policy})

    def _answer_from_books(self, work: Callable) -> None:
        """Open the books for one request and turn any failure into a plain answer for the page."""
        try:
            conn = connect(self.server.db_path)
            try:
                self._json(200, work(conn))
            finally:
                conn.close()
        except BooksError as exc:
            self._json(400, {"error": str(exc)})
        except sqlite3.Error as exc:
            self._json(503, {"error": str(friendly_error(self.server.db_path, exc))})
        except OverflowError:
            self._json(400, {"error": "a number in the request is too large"})

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._json(403, {"error": "this page only answers on the local address"})
            return
        parts = urlparse(self.path)
        route = routes.find("GET", parts.path)
        if route is None:
            self._json(404, {"error": "not found"})
            return
        if route.page:
            # The address printed at startup carries a key, so another account on this computer cannot read the queue.
            if not self._token_ok(parse_qs(parts.query).get("key", [""])[0]):
                self._json(403, {"error": "use the full address the 'web' command printed"})
                return
            self._send_page(route.handler)
            return
        if self._header_token_ok():
            self._answer_from_books(route.handler)

    def do_POST(self) -> None:
        # Read the body before answering. Closing on an unread body resets the connection, and the
        # client then sees a network error in place of the answer below.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._json(400, {"error": "bad request size"})
            return
        raw_body = self.rfile.read(length)
        if not self._host_allowed():
            self._json(403, {"error": "this page only answers on the local address"})
            return
        if not self._header_token_ok():
            return
        route = routes.find("POST", urlparse(self.path).path)
        if route is None:
            self._json(404, {"error": "not found"})
            return
        try:
            body = routes.from_json(raw_body)
        except ValueError as exc:
            self._json(400, {"error": f"bad JSON: {exc}"})
            return
        self._answer_from_books(lambda conn: route.handler(conn, body))


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows this option would let a second server share the port and answer in the first one's place.
    allow_reuse_address = os.name != "nt"

    def __init__(self, db_path: Path, port: int = 0, verbose: bool = False) -> None:
        self.db_path = Path(db_path)
        self.verbose = verbose
        self.token = secrets.token_urlsafe(24)
        super().__init__((LOCAL_ADDRESS, port), ReviewHandler)

    @property
    def url(self) -> str:
        return f"http://{LOCAL_ADDRESS}:{self.server_address[1]}/?key={self.token}"

    def handle_error(self, request, client_address) -> None:
        """A browser closing a tab mid-answer is normal, and anything else is not worth a stack trace on screen."""
        kind, error, _ = sys.exc_info()
        if self.verbose and kind is not None:
            print(f"solo-books: request failed: {kind.__name__}: {error}", file=sys.stderr)
