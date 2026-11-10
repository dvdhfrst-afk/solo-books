"""The local server: who may ask, which handler answers, and how a failure is answered. It listens on 127.0.0.1 only."""
from __future__ import annotations

import os
import secrets
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .. import history
from ..db import connect, friendly_error
from ..errors import BooksError
from . import routes
from .api import ApiError, Request, error_body

MAX_BODY_BYTES = 8_000_000
LOCAL_ADDRESS = "127.0.0.1"
KEY_HEADER = "X-Solo-Books-Token"
# One stalled connection must not hold a thread forever.
REQUEST_TIMEOUT_SECONDS = 30
# A browser says where a request came from. Only this page itself, or a person typing an address, may write.
_SAME_SITE_VALUES = ("same-origin", "none")
_BODY_TYPES = {"json": "application/json", "bytes": "application/octet-stream"}


class ReviewHandler(BaseHTTPRequestHandler):
    server: "ReviewServer"
    server_version = "solo-books"
    sys_version = ""
    timeout = REQUEST_TIMEOUT_SECONDS

    def log_message(self, format: str, *args) -> None:
        if self.server.verbose:
            super().log_message(format, *args)

    def _local_names(self):
        port = self.server.server_address[1]
        return (f"{LOCAL_ADDRESS}:{port}", f"localhost:{port}")

    def _host_allowed(self) -> bool:
        # A hostile web page can point its own name at this address, and the browser would then
        # treat this server as that page's own origin. Requiring a local Host header stops that.
        return self.headers.get("Host", "") in self._local_names()

    def _same_origin(self) -> bool:
        """A write must come from this server's own pages, or from a program that is not a browser."""
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site.lower() not in _SAME_SITE_VALUES:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in tuple(f"http://{name}" for name in self._local_names())

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

    def _json(self, status: int, data: dict, extra: Optional[dict] = None) -> None:
        self._send(status, routes.to_json(data), "application/json", extra)

    def _fail(self, api: bool, status: int, message: str, extra: Optional[dict] = None) -> None:
        """The review page reads {"error": text}; the API answers {"error": {"code", "message"}}."""
        self._json(status, error_body(status, message) if api else {"error": message}, extra)

    def _token_ok(self, offered: str) -> bool:
        return secrets.compare_digest(offered.encode("utf-8", "replace"), self.server.token.encode("utf-8"))

    def _send_page(self, render: Callable[[str, str], str]) -> None:
        nonce = secrets.token_urlsafe(16)
        page = render(self.server.token, nonce)
        policy = (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", {"Content-Security-Policy": policy})

    def _answer_from_books(self, work: Callable) -> None:
        """The review page's JSON: open the books for one request and turn any failure into a plain answer."""
        try:
            conn = connect(self.server.db_path)
            try:
                with history.as_source("web"):
                    answer = work(conn)
                self._json(200, answer)
            finally:
                conn.close()
        except BooksError as exc:
            self._json(400, {"error": str(exc)})
        except sqlite3.Error as exc:
            self._json(503, {"error": str(friendly_error(self.server.db_path, exc))})
        except OverflowError:
            self._json(400, {"error": "a number in the request is too large"})

    def _answer_api(self, route: routes.Route, request: Request) -> None:
        try:
            if route.needs_conn:
                try:
                    conn = connect(self.server.db_path)
                except (BooksError, sqlite3.Error) as exc:
                    message = exc if isinstance(exc, BooksError) else friendly_error(self.server.db_path, exc)
                    raise ApiError(503, str(message)) from None
                try:
                    with history.as_source("web"):
                        result = route.handler(conn, request)
                finally:
                    conn.close()
            else:
                result = route.handler(request)
        except ApiError as exc:
            self._fail(True, exc.status, exc.message)
        except BooksError as exc:
            self._fail(True, 422, str(exc))
        except sqlite3.Error as exc:
            self._fail(True, 503, str(friendly_error(self.server.db_path, exc)))
        except OverflowError:
            self._fail(True, 400, "a number in the request is too large")
        except Exception as exc:  # A caller gets a sentence, not a dropped connection.
            if self.server.verbose:
                print(f"solo-books: request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            self._fail(True, 500, f"this is a bug in the program ({type(exc).__name__}). The step in progress was rolled back.")
        else:
            if isinstance(result, routes.Download):
                # The name is made of letters, digits, dots and hyphens, so it needs no quoting rules.
                self._send(200, result.payload(), result.content_type, {"Content-Disposition": f'attachment; filename="{result.name}"'})
                return
            status, data = result if isinstance(result, tuple) else (200, result)
            self._json(status, data)

    def _read_body(self, api: bool) -> Optional[bytes]:
        """Read the whole body before answering. Closing on an unread body resets the connection, and the
        client then sees a network error in place of the answer. A body too large to read is the exception."""
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self.close_connection = True
            self._fail(api, 400, "send the body with a Content-Length")
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._fail(api, 400, "bad request size")
            return None
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            if api:
                self._fail(True, 413, f"the body is larger than {MAX_BODY_BYTES // 1_000_000} MB. Import a file that large with the 'import' command.", {"Connection": "close"})
            else:
                self._fail(False, 400, "bad request size", {"Connection": "close"})
            return None
        return self.rfile.read(length)

    def _body_type_ok(self, route: routes.Route, raw: bytes) -> bool:
        """A body must say what it is. A form or plain text, which another site can send without asking, is refused."""
        if not raw:
            return True
        wanted = _BODY_TYPES[route.body]
        given = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if given == wanted:
            return True
        self._fail(True, 400, f"send the body with Content-Type: {wanted}")
        return False

    def _handle(self, method: str) -> None:
        parts = urlparse(self.path)
        api = parts.path.startswith(routes.API_PREFIX)
        raw = self._read_body(api)
        if raw is None:
            return
        if not self._host_allowed():
            self._fail(api, 403, "this page only answers on the local address")
            return
        found = routes.find(method, parts.path)
        if found is None:
            allowed = routes.methods_for(parts.path) if api else []
            if allowed:
                self._fail(True, 405, f"{parts.path} answers {', '.join(allowed)}, not {method}", {"Allow": ", ".join(allowed)})
            else:
                self._fail(api, 404, "not found")
            return
        route, params = found
        if route.kind == "page":
            # The address printed at startup carries a key, so another account on this computer cannot read the books.
            if not self._token_ok(parse_qs(parts.query).get("key", [""])[0]):
                self._fail(False, 403, "use the full address the 'web' command printed")
                return
            self._send_page(route.handler)
            return
        # Another site can make a browser send a request here, but it cannot read the page to learn this key.
        if not self._token_ok(self.headers.get(KEY_HEADER, "")):
            message = "missing or wrong page token; reload the page"
            if api:
                message = f"missing or wrong key. Send the key from the address 'web' printed in the {KEY_HEADER} header."
            self._fail(api, 403, message)
            return
        if method != "GET" and not self._same_origin():
            self._fail(api, 403, "a write from another site is refused")
            return
        if route.kind == "json":
            if method == "GET":
                self._answer_from_books(route.handler)
                return
            try:
                body = routes.from_json(raw)
            except ValueError as exc:
                self._fail(False, 400, f"bad JSON: {exc}")
                return
            self._answer_from_books(lambda conn: route.handler(conn, body))
            return
        self._handle_api(method, route, params, parts.query, raw)

    def _handle_api(self, method: str, route: routes.Route, params: Dict[str, int], query: str, raw: bytes) -> None:
        body = None
        if method != "GET":
            if not self._body_type_ok(route, raw):
                return
            if route.body == "bytes":
                body = raw
            elif raw:
                try:
                    body = routes.from_json(raw)
                except ValueError as exc:
                    self._fail(True, 400, f"bad JSON: {exc}")
                    return
            else:
                body = {}
        request = Request(params, parse_qs(query, keep_blank_values=True), body, self.server.db_path, self.server.settings)
        self._answer_api(route, request)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")


class ReviewServer(ThreadingHTTPServer):
    """The local web app and its API, answering on 127.0.0.1 only, with a key made new for each run."""

    daemon_threads = True
    # On Windows this option would let a second server share the port and answer in the first one's place.
    allow_reuse_address = os.name != "nt"

    def __init__(self, db_path: Path, port: int = 0, verbose: bool = False, settings=None) -> None:
        self.db_path = Path(db_path)
        self.verbose = verbose
        self.settings = settings
        self.token = secrets.token_urlsafe(24)
        super().__init__((LOCAL_ADDRESS, port), ReviewHandler)

    @property
    def origin(self) -> str:
        return f"http://{LOCAL_ADDRESS}:{self.server_address[1]}"

    @property
    def url(self) -> str:
        """The review queue's address."""
        return f"{self.origin}/?key={self.token}"

    @property
    def dashboard_url(self) -> str:
        return f"{self.origin}/dashboard?key={self.token}"

    def handle_error(self, request, client_address) -> None:
        """A browser closing a tab mid-answer is normal, and anything else is not worth a stack trace on screen."""
        kind, error, _ = sys.exc_info()
        if self.verbose and kind is not None:
            print(f"solo-books: request failed: {kind.__name__}: {error}", file=sys.stderr)
