"""The local review page, over real HTTP on an unused port. No browser is opened."""
from __future__ import annotations

import http.client
import json
import threading
from datetime import date

from solo_books import ledger, review, rules
from solo_books.web import ReviewServer
from tests.helpers import BooksCase


class ReviewPage(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)
        self.first = ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE 0102")
        self.second = ledger.add_manual(self.conn, self.acct, date(2025, 1, 3), -550, "BLUE FERN CAFE 0103")
        self.third = ledger.add_manual(self.conn, self.acct, date(2025, 1, 4), -5000, "EXAMPLE CARD PAYMENT")
        self.server = ReviewServer(self.db_path, port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def request(self, method: str, path: str, body=None, headers=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(client.close)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        client.request(method, path, body=payload, headers=headers or {})
        response = client.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    def post(self, path: str, body, token=None):
        headers = {"X-Solo-Books-Token": token if token is not None else self.server.token}
        status, data, _ = self.request("POST", path, body, headers)
        return status, json.loads(data)

    def test_listens_only_on_the_local_address(self) -> None:
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.server.url, f"http://127.0.0.1:{self.port}/")

    def test_page_loads_with_a_tight_policy_and_no_outside_resources(self) -> None:
        status, data, headers = self.request("GET", "/")
        page = data.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("Review queue", page)
        self.assertIn(self.server.token, page)
        self.assertNotIn("__NONCE__", page)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)

    def test_queue_groups_rows(self) -> None:
        status, data, _ = self.request("GET", "/api/queue")
        queue = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(queue["remaining"], 3)
        self.assertEqual(queue["groups"][0]["merchant"], "BLUE FERN CAFE")
        self.assertEqual(queue["groups"][0]["total"], "-10.00")
        self.assertEqual(len(queue["groups"][0]["rows"]), 2)
        codes = [c["code"] for c in queue["businesses"][0]["categories"]]
        self.assertIn("3900", codes)

    def test_book_a_group(self) -> None:
        status, result = self.post("/api/categorize", {"ids": [self.first, self.second], "category": "6150"})
        self.assertEqual(status, 200)
        self.assertEqual(result["booked"], 2)
        self.assertEqual(result["category"], "6150 Meals")
        self.assertEqual(len(review.queue(self.conn)), 1)

    def test_book_with_a_rule_and_undo_all_of_it(self) -> None:
        extra = ledger.add_manual(self.conn, self.acct, date(2025, 1, 9), -700, "BLUE FERN CAFE 0109")
        status, result = self.post(
            "/api/categorize", {"ids": [self.first], "category": "6150", "rule_pattern": "BLUE FERN CAFE"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["by_rule"], 3)
        self.assertEqual(ledger.get_transaction(self.conn, extra)["category_source"], "rule")
        self.assertEqual(len(rules.list_rules(self.conn, self.biz["id"])), 1)

        status, undone = self.post("/api/undo", {"ids": result["ids"], "rule_id": result["rule_id"]})
        self.assertEqual(status, 200)
        self.assertEqual(rules.list_rules(self.conn, self.biz["id"]), [])
        self.assertEqual(len(review.queue(self.conn)), 4)
        self.assertGreaterEqual(undone["returned"], 3)

    def test_post_needs_the_page_token(self) -> None:
        body = {"ids": [self.first], "category": "6150"}
        self.assertEqual(self.post("/api/categorize", body, token="wrong")[0], 403)
        status, _, _ = self.request("POST", "/api/categorize", body)
        self.assertEqual(status, 403)
        self.assertEqual(len(review.queue(self.conn)), 3)

    def test_foreign_host_header_is_refused(self) -> None:
        headers = {"Host": "attacker.example:80"}
        self.assertEqual(self.request("GET", "/api/queue", headers=headers)[0], 403)
        headers["X-Solo-Books-Token"] = self.server.token
        self.assertEqual(self.request("POST", "/api/categorize", {"ids": [1], "category": "6150"}, headers)[0], 403)

    def test_bad_requests_get_plain_errors(self) -> None:
        self.assertEqual(self.post("/api/categorize", {"ids": "all", "category": "6150"})[0], 400)
        self.assertEqual(self.post("/api/categorize", {"ids": [self.first]})[0], 400)
        status, result = self.post("/api/categorize", {"ids": [self.first], "category": "nope"})
        self.assertEqual(status, 400)
        self.assertIn("no category", result["error"])
        self.assertEqual(self.post("/api/categorize", {"ids": [99999], "category": "6150"})[0], 400)
        self.assertEqual(self.post("/api/nothing", {})[0], 404)
        self.assertEqual(self.request("GET", "/nothing")[0], 404)
        self.assertEqual(len(review.queue(self.conn)), 3)

    def test_description_reaches_the_page_as_data_only(self) -> None:
        ledger.add_manual(self.conn, self.acct, date(2025, 1, 5), -100, "<img src=x onerror=alert(1)>")
        _, data, headers = self.request("GET", "/api/queue")
        self.assertIn("application/json", headers["Content-Type"])
        # The page builds rows with textContent, so this stays text.
        _, page, _ = self.request("GET", "/")
        self.assertNotIn(b"innerHTML", page)
        self.assertIn(b"<img src=x", data)
