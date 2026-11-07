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
        self.assertEqual(self.server.url, f"http://127.0.0.1:{self.port}/?key={self.server.token}")

    def test_page_loads_with_a_tight_policy_and_no_outside_resources(self) -> None:
        status, data, headers = self.request("GET", f"/?key={self.server.token}")
        page = data.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("Review queue", page)
        self.assertIn(self.server.token, page)
        self.assertNotIn("__NONCE__", page)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)

    def test_queue_groups_rows(self) -> None:
        status, data, _ = self.request("GET", "/api/queue", headers={"X-Solo-Books-Token": self.server.token})
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

    def test_unticked_row_stays_unbooked_when_a_rule_is_made(self) -> None:
        unshown = ledger.add_manual(self.conn, self.acct, date(2025, 1, 9), -700, "BLUE FERN CAFE 0109")
        status, result = self.post(
            "/api/categorize",
            {"ids": [self.first], "category": "6150", "rule_pattern": "BLUE FERN CAFE", "unticked_ids": [self.second]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["by_rule"], 2)
        self.assertIsNone(ledger.get_transaction(self.conn, self.second)["category_id"])
        self.assertEqual(ledger.get_transaction(self.conn, unshown)["category_source"], "rule")

    def test_unticked_row_stays_unbooked_through_later_rule_runs(self) -> None:
        self.post(
            "/api/categorize",
            {"ids": [self.first], "category": "6150", "rule_pattern": "BLUE FERN CAFE", "unticked_ids": [self.second]},
        )
        # Every way the rules run again over the queue.
        self.post("/api/categorize", {"ids": [self.third], "category": "3900", "rule_pattern": "EXAMPLE CARD"})
        self.ok("rules", "apply", "--business", "Business A")
        self.ok("rules", "apply", "--business", "Business A", "--recheck")
        self.ok("rules", "add", "--business", "Business A", "--contains", "NOTHING ELSE", "--category", "6150")
        self.assertIsNone(ledger.get_transaction(self.conn, self.second)["category_id"])
        # It is still the person's to book by hand.
        self.assertEqual(self.post("/api/categorize", {"ids": [self.second], "category": "6150"})[1]["booked"], 1)

    def test_undoing_the_rule_forgets_what_it_held_back(self) -> None:
        _, result = self.post(
            "/api/categorize",
            {"ids": [self.first], "category": "6150", "rule_pattern": "BLUE FERN CAFE", "unticked_ids": [self.second]},
        )
        self.post("/api/undo", {"ids": result["ids"], "rule_id": result["rule_id"]})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rule_exceptions").fetchone()[0], 0)
        _, again = self.post("/api/categorize", {"ids": [self.first], "category": "6150", "rule_pattern": "BLUE FERN CAFE"})
        self.assertEqual(again["by_rule"], 2)

    def test_bad_unticked_rows_are_refused_and_change_nothing(self) -> None:
        other = self.account(self.business("Business B"), "B Bank")
        foreign = ledger.add_manual(self.conn, other, date(2025, 1, 5), -100, "BLUE FERN CAFE")
        base = {"ids": [self.first], "category": "6150", "rule_pattern": "BLUE FERN CAFE"}
        for unticked in ("all", [self.first], [99999], [foreign]):
            self.assertEqual(self.post("/api/categorize", dict(base, unticked_ids=unticked))[0], 400)
        self.assertEqual(rules.list_rules(self.conn, self.biz["id"]), [])
        self.assertEqual(len(review.queue(self.conn)), 4)

    def test_the_page_sends_the_unticked_rows(self) -> None:
        _, page, _ = self.request("GET", f"/?key={self.server.token}")
        self.assertIn(b"unticked_ids", page)

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
        _, data, headers = self.request("GET", "/api/queue", headers={"X-Solo-Books-Token": self.server.token})
        self.assertIn("application/json", headers["Content-Type"])
        # The page builds rows with textContent, so this stays text.
        _, page, _ = self.request("GET", f"/?key={self.server.token}")
        self.assertNotIn(b"innerHTML", page)
        self.assertIn(b"<img src=x", data)

    def test_the_page_and_the_queue_need_the_key_from_startup(self) -> None:
        self.assertEqual(self.request("GET", "/")[0], 403)
        self.assertEqual(self.request("GET", "/?key=wrong")[0], 403)
        self.assertEqual(self.request("GET", "/api/queue")[0], 403)
        self.assertEqual(self.request("GET", "/api/queue", headers={"X-Solo-Books-Token": "wrong"})[0], 403)

    def test_booleans_and_huge_numbers_are_refused_plainly(self) -> None:
        for body in ({"ids": [True], "category": "6150"}, {"ids": [10 ** 30], "category": "6150"}, {"ids": [], "rule_id": True}):
            route = "/api/undo" if "rule_id" in body else "/api/categorize"
            status, result = self.post(route, body)
            self.assertEqual(status, 400, body)
            self.assertIn("error", result)

    def test_a_hostile_description_reaches_the_page_only_as_data(self) -> None:
        ledger.add_manual(self.conn, self.acct, date(2025, 1, 5), -100, "<img src=x onerror=alert(1)>")
        page = self.request("GET", f"/?key={self.server.token}")[1].decode("utf-8")
        self.assertNotIn("<img src=x", page)
        self.assertNotIn(".innerHTML", page)
        data = json.loads(self.request("GET", "/api/queue", headers={"X-Solo-Books-Token": self.server.token})[1])
        found = [row["description"] for group in data["groups"] for row in group["rows"]]
        self.assertIn("<img src=x onerror=alert(1)>", found)

    def test_a_stalled_connection_does_not_block_the_next_person(self) -> None:
        import socket

        stalled = socket.create_connection(("127.0.0.1", self.port))
        self.addCleanup(stalled.close)
        stalled.sendall(b"POST /api/categorize HTTP/1.1\r\n")
        self.assertEqual(self.request("GET", "/api/queue", headers={"X-Solo-Books-Token": self.server.token})[0], 200)

    def test_damaged_books_answer_with_a_message_not_a_dropped_connection(self) -> None:
        self.conn.close()
        data = bytearray(self.db_path.read_bytes())
        data[:16] = b"\x00" * 16
        self.db_path.write_bytes(bytes(data))
        status, body, _ = self.request("GET", "/api/queue", headers={"X-Solo-Books-Token": self.server.token})
        self.assertEqual(status, 400)
        self.assertIn("damaged", json.loads(body)["error"])

    def test_the_same_port_cannot_be_taken_twice(self) -> None:
        with self.assertRaises(OSError):
            ReviewServer(self.db_path, port=self.port)

    def test_a_long_queue_is_drawn_in_pages(self) -> None:
        page = self.request("GET", f"/?key={self.server.token}")[1].decode("utf-8")
        self.assertIn("GROUPS_AT_A_TIME", page)

