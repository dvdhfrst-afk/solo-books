"""A small local page for working through the review queue. It listens on 127.0.0.1 only."""
from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .books import find_category, list_businesses, list_categories
from .db import connect, transaction
from .errors import BooksError
from .ledger import get_transaction, uncategorize
from .money import format_cents
from .review import book_rows, group_rows, queue
from .rules import delete_rule

PAGE_PATH = Path(__file__).resolve().parent / "data" / "review.html"
MAX_BODY_BYTES = 1_000_000
LOCAL_ADDRESS = "127.0.0.1"


def queue_payload(conn) -> dict:
    rows = queue(conn)
    groups = group_rows(rows)
    businesses = []
    for business in list_businesses(conn):
        businesses.append(
            {
                "id": business["id"],
                "name": business["name"],
                "categories": [
                    {"code": c["code"], "name": c["name"], "kind": c["kind"]}
                    for c in list_categories(conn, business["id"])
                ],
            }
        )
    return {
        "remaining": len(rows),
        "businesses": businesses,
        "groups": [
            {
                "business_id": group.business_id,
                "business": group.business,
                "merchant": group.merchant,
                "total": format_cents(group.total_cents),
                "rows": [
                    {
                        "id": row["id"],
                        "date": row["posted_on"],
                        "account": row["account_name"],
                        "amount": format_cents(row["amount_cents"]),
                        "description": row["description"],
                    }
                    for row in group.rows
                ],
            }
            for group in groups
        ],
    }


def categorize_request(conn, body: dict) -> dict:
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        raise BooksError("ids must be a list of transaction numbers")
    if not isinstance(body.get("category"), str):
        raise BooksError("category is required")
    business_id = get_transaction(conn, ids[0])["business_id"]
    category = find_category(conn, business_id, body["category"])
    pattern = body.get("rule_pattern") or None
    if pattern is not None and not isinstance(pattern, str):
        raise BooksError("rule_pattern must be text")
    unticked = body.get("unticked_ids", [])
    if not isinstance(unticked, list) or not all(isinstance(i, int) for i in unticked):
        raise BooksError("unticked_ids must be a list of transaction numbers")
    result = book_rows(conn, business_id, ids, category, rule_pattern=pattern, held_back=unticked)
    result["category"] = f"{category['code']} {category['name']}"
    result["ids"] = ids
    return result


def undo_request(conn, body: dict) -> dict:
    """Put rows back in the queue; if the action made a rule, remove it and what it booked."""
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise BooksError("ids must be a list of transaction numbers")
    rule_id = body.get("rule_id")
    if rule_id is not None and not isinstance(rule_id, int):
        raise BooksError("rule_id must be a number")
    with transaction(conn):
        count = uncategorize(conn, ids)
        if rule_id is not None:
            booked = conn.execute("SELECT id FROM transactions WHERE rule_id = ?", (rule_id,)).fetchall()
            count += uncategorize(conn, [row["id"] for row in booked])
            delete_rule(conn, rule_id)
    return {"returned": count}


class ReviewHandler(BaseHTTPRequestHandler):
    server: "ReviewServer"
    server_version = "solo-books"
    sys_version = ""

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
        self._send(status, json.dumps(data).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._json(403, {"error": "this page only answers on the local address"})
            return
        route = urlparse(self.path).path
        if route == "/":
            page = PAGE_PATH.read_text(encoding="utf-8")
            nonce = secrets.token_urlsafe(16)
            page = page.replace("__TOKEN__", self.server.token).replace("__NONCE__", nonce)
            policy = (
                f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            )
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", {"Content-Security-Policy": policy})
            return
        if route == "/api/queue":
            conn = connect(self.server.db_path)
            try:
                self._json(200, queue_payload(conn))
            finally:
                conn.close()
            return
        self._json(404, {"error": "not found"})

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
        # Another site can make a browser send a POST here, but it cannot read the page to learn this token.
        offered = self.headers.get("X-Solo-Books-Token", "").encode("utf-8", "replace")
        if not secrets.compare_digest(offered, self.server.token.encode("utf-8")):
            self._json(403, {"error": "missing or wrong page token; reload the page"})
            return
        route = urlparse(self.path).path
        handlers = {"/api/categorize": categorize_request, "/api/undo": undo_request}
        handler = handlers.get(route)
        if handler is None:
            self._json(404, {"error": "not found"})
            return
        try:
            body = json.loads(raw_body.decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("expected an object")
        except ValueError as exc:
            self._json(400, {"error": f"bad JSON: {exc}"})
            return
        conn = connect(self.server.db_path)
        try:
            self._json(200, handler(conn, body))
        except BooksError as exc:
            self._json(400, {"error": str(exc)})
        finally:
            conn.close()


class ReviewServer(HTTPServer):
    def __init__(self, db_path: Path, port: int = 0, verbose: bool = False) -> None:
        self.db_path = Path(db_path)
        self.verbose = verbose
        self.token = secrets.token_urlsafe(24)
        super().__init__((LOCAL_ADDRESS, port), ReviewHandler)

    @property
    def url(self) -> str:
        return f"http://{LOCAL_ADDRESS}:{self.server_address[1]}/"
