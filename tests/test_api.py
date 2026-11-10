"""The HTTP API under /api/v1/, through a real server with the real key: every resource, and every refusal."""
from __future__ import annotations

import json
from datetime import date

from solo_books import books, ledger, review, rules
from solo_books.csvmap import Mapping
from tests.helpers import REPO_ROOT
from tests.web_helpers import ServerCase

CSV_MAPPING = Mapping(date_column="Date", date_format="%m/%d/%Y", description_column="Description", amount_column="Amount")
GOOD_CSV = b"Date,Description,Amount\n03/02/2025,BLUE FERN CAFE 0302,-4.50\n03/03/2025,CLIENT NORTHWIND INV 7,1200.00\n"


class ApiCase(ServerCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = books.add_account(
            self.conn, self.biz, "A Checking", "checking", "Example Bank", 100000, date(2025, 1, 1), CSV_MAPPING.to_json()
        )
        self.cafe = ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE 0102")
        self.cafe2 = ledger.add_manual(self.conn, self.acct, date(2025, 1, 3), -550, "BLUE FERN CAFE 0103")
        self.rent = ledger.add_manual(self.conn, self.acct, date(2025, 2, 6), -85000, "REDWOOD PLAZA RENT")
        self.client_row = ledger.add_manual(
            self.conn, self.acct, date(2025, 2, 7), 300000, "CLIENT HARBOR LANE INV 1", self.category(self.biz, "4000")
        )

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]


class Guarding(ApiCase):
    def test_a_missing_or_wrong_key_is_refused_for_reads_and_writes(self) -> None:
        for key in ("", "wrong"):
            self.assertIn("X-Solo-Books-Token", self.refused(403, "GET", "status", key=key))
            self.refused(403, "POST", f"transactions/{self.cafe}/category", {"category": "6150"}, key=key)
            self.refused(403, "DELETE", f"transactions/{self.cafe}", key=key)
        self.assertIsNone(ledger.get_transaction(self.conn, self.cafe)["category_id"])
        self.assertEqual(self.count(), 4)

    def test_the_error_code_names_the_status(self) -> None:
        _, data, _ = self.call("GET", "status", key="wrong")
        self.assertEqual(data["error"]["code"], "forbidden")
        _, data, _ = self.call("GET", "nothing")
        self.assertEqual(data["error"]["code"], "not_found")

    def test_a_foreign_host_is_refused(self) -> None:
        self.refused(403, "GET", "status", headers={"Host": "attacker.example"})
        self.refused(403, "POST", "rules/apply", {}, headers={"Host": f"attacker.example:{self.port}"})

    def test_a_cross_site_write_is_refused_and_changes_nothing(self) -> None:
        body = {"category": "6150"}
        path = f"transactions/{self.cafe}/category"
        for headers in (
            {"Origin": "http://attacker.example"},
            {"Origin": f"http://127.0.0.1:{self.port + 1}"},
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
        ):
            self.assertIn("another site", self.refused(403, "POST", path, body, headers=headers), headers)
        self.refused(403, "DELETE", f"transactions/{self.cafe}", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertIsNone(ledger.get_transaction(self.conn, self.cafe)["category_id"])
        self.assertEqual(self.count(), 4)

    def test_the_app_s_own_pages_may_write(self) -> None:
        same = {"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"}
        status, _, _ = self.call("POST", f"transactions/{self.cafe}/category", {"category": "6150"}, headers=same)
        self.assertEqual(status, 200)
        status, _, _ = self.call("POST", f"transactions/{self.cafe}/note", {"note": "x"}, headers={"Origin": f"http://localhost:{self.port}"})
        self.assertEqual(status, 200)

    def test_a_body_must_say_it_is_json(self) -> None:
        path = f"transactions/{self.cafe}/category"
        payload = json.dumps({"category": "6150"}).encode("utf-8")
        for content_type in (None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
            headers = {"X-Solo-Books-Token": self.server.token}
            if content_type:
                headers["Content-Type"] = content_type
            status, data, _ = self.raw("POST", "/api/v1/" + path, payload, headers)
            self.assertEqual(status, 400, content_type)
            self.assertIn("application/json", json.loads(data)["error"]["message"])
        self.assertIsNone(ledger.get_transaction(self.conn, self.cafe)["category_id"])

    def test_an_oversize_body_is_refused_before_it_is_read(self) -> None:
        headers = {"X-Solo-Books-Token": self.server.token, "Content-Type": "application/json", "Content-Length": "9000000"}
        status, data, answer = self.raw("POST", "/api/v1/transactions", b"{}", headers)
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(data)["error"]["code"], "too_large")
        self.assertEqual(answer.get("Connection"), "close")

    def test_bad_json_and_a_body_that_is_not_an_object_are_400(self) -> None:
        headers = {"X-Solo-Books-Token": self.server.token, "Content-Type": "application/json"}
        for payload in (b"{not json", b"[1, 2]", b"\xff\xfe", b"[" * 100000):
            status, data, _ = self.raw("POST", "/api/v1/transactions", payload, headers)
            self.assertEqual(status, 400, payload[:10])
            self.assertIn("bad JSON", json.loads(data)["error"]["message"])

    def test_a_body_without_a_length_is_refused(self) -> None:
        headers = {"X-Solo-Books-Token": self.server.token, "Content-Type": "application/json", "Transfer-Encoding": "chunked"}
        status, _, _ = self.raw("POST", "/api/v1/rules/apply", b"2\r\n{}\r\n0\r\n\r\n", headers)
        self.assertEqual(status, 400)

    def test_unknown_addresses_and_wrong_methods(self) -> None:
        self.refused(404, "GET", "nothing")
        self.refused(404, "GET", "transactions/abc")
        self.refused(404, "GET", "transactions/0")
        status, data, headers = self.call("DELETE", "status")
        self.assertEqual(status, 405)
        self.assertEqual(data["error"]["code"], "method_not_allowed")
        self.assertEqual(headers["Allow"], "GET")
        status, _, headers = self.call("PUT", f"transactions/{self.cafe}", {})
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "DELETE, GET")
        self.assertEqual(self.call("PATCH", "rules", {})[0], 405)
        self.assertEqual(self.call("GET", "rules/test")[0], 405)

    def test_unknown_parameters_and_fields_are_refused(self) -> None:
        self.assertIn("uncategorised", self.refused(400, "GET", "transactions", query={"uncategorised": "true"}))
        self.assertIn("colour", self.refused(400, "POST", f"transactions/{self.cafe}/category", {"category": "6150", "colour": 1}))
        self.refused(400, "GET", "transactions", query={"limit": "0"})
        self.refused(400, "GET", "transactions", query={"limit": "1001"})
        self.refused(400, "GET", "transactions", query={"offset": "-1"})
        self.refused(400, "GET", "transactions", query={"uncategorized": "maybe"})
        self.refused(400, "GET", "transactions", query={"business": "1e3"})

    def test_answers_carry_the_security_headers(self) -> None:
        _, _, headers = self.call("GET", "status")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("application/json", headers["Content-Type"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_booleans_and_huge_numbers_are_refused_plainly(self) -> None:
        self.refused(400, "POST", "transactions", {"account_id": True, "date": "2025-03-01", "amount": "-1.00", "description": "X"})
        self.refused(400, "POST", "transactions", {"account_id": 10 ** 30, "date": "2025-03-01", "amount": "-1.00", "description": "X"})
        self.refused(400, "POST", "transactions", {"account_id": self.acct["id"], "date": "2025-03-01", "amount": -1.5, "description": "X"})
        self.refused(400, "POST", "rules", {"match": "contains", "pattern": "X", "category": "6150", "priority": 2 ** 70})
        self.assertEqual(self.count(), 4)

    def test_damaged_books_answer_unavailable_and_the_check_still_reads_them(self) -> None:
        self.conn.close()
        data = bytearray(self.db_path.read_bytes())
        data[:16] = b"\x00" * 16
        self.db_path.write_bytes(bytes(data))
        self.assertIn("solo-books file", self.refused(503, "GET", "status"))
        report = self.ok_call("GET", "check")
        self.assertFalse(report["healthy"])
        self.assertTrue(report["problems"])


class Reading(ApiCase):
    def test_status_says_what_waits_and_how_far_each_account_is_reconciled(self) -> None:
        status = self.ok_call("GET", "status")
        self.assertEqual(status["waiting"], 3)
        self.assertIsNone(status["last_backup"])
        self.assertEqual(status["books"], str(self.db_path))
        account = status["businesses"][0]["accounts"][0]
        self.assertEqual((account["name"], account["rows"], account["latest_row"]), ("A Checking", 4, "2025-02-07"))
        self.assertIsNone(account["reconciled_through"])
        self.ok("backup", str(self.dir / "copy.sqlite3"))
        self.assertIsNotNone(self.ok_call("GET", "status")["last_backup"])

    def test_status_on_empty_books_says_what_to_do_next(self) -> None:
        self.ok("account", "delete", "A Checking", "--with-rows")
        self.assertIn("account add", self.ok_call("GET", "status")["next"])

    def test_businesses_and_their_chart(self) -> None:
        listed = self.ok_call("GET", "businesses")["businesses"]
        self.assertEqual([(b["name"], b["accounts"], b["waiting"]) for b in listed], [("Business A", 1, 3)])
        chart = self.ok_call("GET", f"businesses/{self.biz['id']}/chart")
        meals = [c for c in chart["categories"] if c["code"] == "6150"][0]
        self.assertEqual(meals["kind"], "expense")
        self.assertEqual(meals["line"], "24b")
        transfer = [c for c in chart["categories"] if c["kind"] == "transfer"][0]
        self.assertIsNone(transfer["line"])
        self.ok("chart", "retire", "6290", "--business", "Business A")
        codes = [c["code"] for c in self.ok_call("GET", f"businesses/{self.biz['id']}/chart")["categories"]]
        self.assertNotIn("6290", codes)
        retired = self.ok_call("GET", f"businesses/{self.biz['id']}/chart", query={"include_retired": "true"})
        self.assertIn("6290", [c["code"] for c in retired["categories"]])
        self.assertEqual(self.ok_call("GET", f"businesses/{self.biz['id']}/chart", query={"year": "2025"})["tax_year"], 2025)
        self.refused(404, "GET", "businesses/999/chart")
        self.refused(422, "GET", f"businesses/{self.biz['id']}/chart", query={"year": "1999"})

    def test_accounts_show_balances_as_a_statement_prints_them(self) -> None:
        card = self.account(self.biz, "A Card", kind="credit_card", opening=-42500)
        ledger.add_manual(self.conn, card, date(2025, 1, 9), -1000, "STAPLE HUT 1")
        found = {a["name"]: a for a in self.ok_call("GET", "accounts")["accounts"]}
        checking, owed = found["A Checking"], found["A Card"]
        self.assertEqual(checking["balance_cents"], 100000 - 450 - 550 - 85000 + 300000)
        self.assertEqual(checking["balance_printed"], "3,140.00")
        self.assertEqual(checking["balance_on"], "2025-02-07")
        self.assertEqual((owed["balance_cents"], owed["balance_printed"], owed["printed_as_owed"]), (-43500, "435.00", True))
        self.assertEqual(owed["opening_balance_printed"], "425.00")
        self.assertEqual(len(self.ok_call("GET", "accounts", query={"business": str(self.biz["id"])})["accounts"]), 2)
        self.refused(422, "GET", "accounts", query={"business": "999"})

    def test_one_account_with_its_mapping_and_reconciliations(self) -> None:
        self.ok("reconcile", "run", "A Checking", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "1000.00", "--end", "990.00")
        account = self.ok_call("GET", f"accounts/{self.acct['id']}")
        self.assertEqual(account["mapping"]["date_column"], "Date")
        self.assertEqual(account["rows_before_opening"], 0)
        self.assertEqual([(r["from"], r["status"], r["statement_end_printed"]) for r in account["reconciliations"]], [("2025-01-01", "open", "990.00")])
        self.refused(404, "GET", "accounts/999")

    def test_transactions_filter_and_count(self) -> None:
        listed = self.ok_call("GET", "transactions")
        self.assertEqual(listed["total"], 4)
        self.assertEqual(listed["net_cents"], -450 - 550 - 85000 + 300000)
        self.assertEqual([t["id"] for t in listed["transactions"]], [self.cafe, self.cafe2, self.rent, self.client_row])
        row = listed["transactions"][0]
        self.assertEqual((row["amount_cents"], row["amount"], row["merchant"], row["category"]), (-450, "-4.50", "BLUE FERN CAFE", None))

        def ids(**query):
            return [t["id"] for t in self.ok_call("GET", "transactions", query=query)["transactions"]]

        self.assertEqual(ids(uncategorized="true"), [self.cafe, self.cafe2, self.rent])
        self.assertEqual(ids(category="4000"), [self.client_row])
        self.assertEqual(ids(category="Sales"), [self.client_row])
        self.assertEqual(ids(period="2025-02"), [self.rent, self.client_row])
        self.assertEqual(ids(period="2025-01-03..2025-02-06"), [self.cafe2, self.rent])
        self.assertEqual(ids(search="fern"), [self.cafe, self.cafe2])
        self.assertEqual(ids(account=str(self.acct["id"]), business=str(self.biz["id"]), limit="2", offset="1"), [self.cafe2, self.rent])
        self.assertEqual(ids(order="newest", limit="1"), [self.client_row])
        page = self.ok_call("GET", "transactions", query={"limit": "1", "offset": "3"})
        self.assertEqual((page["total"], page["limit"], page["offset"], len(page["transactions"])), (4, 1, 3, 1))
        self.assertEqual(self.ok_call("GET", "transactions", query={"offset": "50"})["transactions"], [])

    def test_transaction_filters_refuse_plainly(self) -> None:
        self.assertIn("not a period", self.refused(422, "GET", "transactions", query={"period": "March"}))
        self.assertIn("no category", self.refused(422, "GET", "transactions", query={"category": "nope"}))
        self.refused(400, "GET", "transactions", query={"category": "6150", "uncategorized": "true"})
        self.refused(400, "GET", "transactions", query={"order": "sideways"})
        self.refused(422, "GET", "transactions", query={"account": "999"})
        self.business("Business B")
        self.assertIn("business=ID", self.refused(400, "GET", "transactions", query={"category": "6150"}))
        self.assertEqual(len(self.ok_call("GET", "transactions", query={"category": "4000", "account": str(self.acct["id"])})["transactions"]), 1)

    def test_one_transaction_says_how_it_was_booked_and_where_it_came_from(self) -> None:
        self.ok("rules", "add", "--contains", "REDWOOD", "--category", "6105")
        self.ok("import", "A Checking", str(self.write("march.csv", GOOD_CSV.decode())))
        rent = self.ok_call("GET", f"transactions/{self.rent}")
        self.assertEqual(rent["booked_by"], "rule")
        self.assertEqual(rent["rule"]["match"], "contains")
        self.assertEqual(rent["category"]["code"], "6105")
        self.assertEqual(rent["imports"], [])
        imported = [t for t in self.ok_call("GET", "transactions", query={"period": "2025-03"})["transactions"]][0]
        detail = self.ok_call("GET", f"transactions/{imported['id']}")
        self.assertEqual([i["file_name"] for i in detail["imports"]], ["march.csv"])
        self.assertEqual(detail["held_back_from_rules"], [])
        self.refused(404, "GET", "transactions/99999")

    def test_a_hostile_description_comes_back_only_as_data(self) -> None:
        hostile = "<img src=x onerror=alert(1)>"
        new_id = ledger.add_manual(self.conn, self.acct, date(2025, 1, 5), -100, hostile)
        status, raw, headers = self.raw("GET", f"/api/v1/transactions/{new_id}", headers={"X-Solo-Books-Token": self.server.token})
        self.assertIn("application/json", headers["Content-Type"])
        self.assertEqual(json.loads(raw)["description"], hostile)

    def test_the_check_reads_without_changing_anything(self) -> None:
        before = self.db_path.read_bytes()
        report = self.ok_call("GET", "check")
        self.assertTrue(report["healthy"])
        self.assertEqual(report["counts"]["rows"], 4)
        self.assertEqual(report["schema_version"], report["reads_up_to"])
        self.assertEqual(self.db_path.read_bytes(), before)


class BookingRows(ApiCase):
    def test_book_a_row_and_send_it_back(self) -> None:
        booked = self.ok_call("POST", f"transactions/{self.cafe}/category", {"category": "Meals"})["transaction"]
        self.assertEqual((booked["category"]["code"], booked["booked_by"]), ("6150", "manual"))
        self.assertEqual(len(review.queue(self.conn)), 2)
        cleared = self.ok_call("POST", f"transactions/{self.cafe}/category", {"category": None})["transaction"]
        self.assertIsNone(cleared["category"])
        self.assertEqual(len(review.queue(self.conn)), 3)

    def test_a_category_the_cli_refuses_is_refused_with_the_same_sentence(self) -> None:
        message = self.refused(422, "POST", f"transactions/{self.cafe}/category", {"category": "nope"})
        self.assertIn(message, self.fails("categorize", "nope", str(self.cafe)))
        self.assertIn("too short", self.refused(422, "POST", f"transactions/{self.cafe}/category", {"category": "Me"}))
        self.ok("chart", "retire", "6290", "--business", "Business A")
        self.assertIn("retired", self.refused(422, "POST", f"transactions/{self.cafe}/category", {"category": "6290"}))
        self.refused(400, "POST", f"transactions/{self.cafe}/category", {})
        self.refused(400, "POST", f"transactions/{self.cafe}/category", {"category": 6150})
        self.refused(404, "POST", "transactions/99999/category", {"category": "6150"})
        self.assertEqual(len(review.queue(self.conn)), 3)

    def test_a_note_is_saved_and_cleaned(self) -> None:
        noted = self.ok_call("POST", f"transactions/{self.cafe}/note", {"note": "Lunch with\na client"})["transaction"]
        self.assertEqual(noted["note"], "Lunch with a client")
        self.assertEqual(ledger.get_transaction(self.conn, self.cafe)["note"], "Lunch with a client")
        self.refused(400, "POST", f"transactions/{self.cafe}/note", {})
        self.refused(404, "POST", "transactions/99999/note", {"note": "x"})

    def test_add_a_row_by_hand(self) -> None:
        body = {"account_id": self.acct["id"], "date": "2025-03-27", "amount": "-15.00", "description": "CITY PARKING GARAGE", "category": "Travel", "note": "Missed by the export"}
        added = self.ok_call("POST", "transactions", body, status=201)
        row = added["transaction"]
        self.assertEqual((row["amount_cents"], row["category"]["code"], row["booked_by"], row["note"]), (-1500, "6140", "manual", "Missed by the export"))
        self.assertEqual(added["warnings"], [])
        self.assertEqual(row["imports"], [])
        plain = self.ok_call("POST", "transactions", {"account_id": self.acct["id"], "date": "2025-03-28", "amount": "$1,200.50", "description": "DEPOSIT"}, status=201)
        self.assertIsNone(plain["transaction"]["category"])
        self.assertEqual(plain["transaction"]["amount_cents"], 120050)

    def test_adding_refuses_what_the_cli_refuses(self) -> None:
        base = {"account_id": self.acct["id"], "date": "2025-03-27", "amount": "-15.00", "description": "CITY PARKING"}
        zero = self.refused(422, "POST", "transactions", dict(base, amount="0.00"))
        self.assertIn(zero, self.fails("txn", "add", "A Checking", "--date", "2025-03-27", "--amount", "0", "--description", "X"))
        self.assertIn("not a date", self.refused(422, "POST", "transactions", dict(base, date="03/27/2025")))
        self.assertIn("not an amount", self.refused(422, "POST", "transactions", dict(base, amount="twelve")))
        self.assertIn("fractions of a cent", self.refused(422, "POST", "transactions", dict(base, amount="1.005")))
        self.assertIn("needs a description", self.refused(422, "POST", "transactions", dict(base, description="  ")))
        self.assertIn("no account 999", self.refused(422, "POST", "transactions", dict(base, account_id=999)))
        self.assertIn("no category", self.refused(422, "POST", "transactions", dict(base, category="nope")))
        for missing in ("account_id", "date", "amount", "description"):
            self.assertIn(missing, self.refused(400, "POST", "transactions", {k: v for k, v in base.items() if k != missing}))
        self.assertEqual(self.count(), 4)

    def test_adding_inside_a_finished_period_warns(self) -> None:
        self.ok("reconcile", "run", "A Checking", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "1000.00", "--end", "990.00")
        self.ok("reconcile", "finish", "1")
        added = self.ok_call("POST", "transactions", {"account_id": self.acct["id"], "date": "2025-01-20", "amount": "-1.00", "description": "LATE ROW"}, status=201)
        self.assertIn("finished reconciliation 1", added["warnings"][0])

    def test_delete_a_row_and_keep_it_out_of_later_imports(self) -> None:
        self.ok("import", "A Checking", str(self.write("march.csv", GOOD_CSV.decode())))
        imported = self.ok_call("GET", "transactions", query={"period": "2025-03"})["transactions"][0]
        gone = self.ok_call("DELETE", f"transactions/{imported['id']}")
        self.assertEqual(gone["deleted"], imported["id"])
        self.refused(404, "DELETE", f"transactions/{imported['id']}")
        self.ok("import", "A Checking", str(self.write("march-again.csv", GOOD_CSV.decode())))
        self.assertEqual(len(self.ok_call("GET", "transactions", query={"period": "2025-03"})["transactions"]), 1)

    def test_a_row_in_a_finished_period_cannot_be_deleted(self) -> None:
        self.ok("reconcile", "run", "A Checking", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "1000.00", "--end", "990.00")
        self.ok("reconcile", "finish", "1")
        message = self.refused(409, "DELETE", f"transactions/{self.cafe}")
        self.assertIn("finished reconciliation 1", message)
        self.assertIn(message, self.fails("txn", "delete", str(self.cafe)))
        self.assertEqual(self.count(), 4)


class Importing(ApiCase):
    def upload(self, data: bytes, status: int = 201, **query) -> dict:
        query = dict({"account": str(self.acct["id"]), "filename": "march.csv"}, **query)
        return self.ok_call("POST", "imports", query=query, upload=data, status=status)

    def test_a_dry_run_says_what_would_happen_and_saves_nothing(self) -> None:
        result = self.upload(GOOD_CSV, status=200, dry_run="true")
        self.assertTrue(result["dry_run"])
        self.assertIsNone(result["import_id"])
        self.assertEqual((result["rows_read"], result["rows_new"], result["waiting_for_review"]), (2, 2, 2))
        self.assertEqual([s["amount"] for s in result["samples"]], ["-4.50", "1,200.00"])
        self.assertEqual((result["earliest"], result["latest"]), ("2025-03-02", "2025-03-03"))
        self.assertEqual(self.count(), 4)
        self.assertEqual(self.ok_call("GET", "imports")["imports"], [])

    def test_import_list_and_undo(self) -> None:
        self.ok("rules", "add", "--starts", "CLIENT ", "--category", "4000")
        result = self.upload(GOOD_CSV)
        self.assertEqual((result["rows_new"], result["booked_by_rules"], result["waiting_for_review"]), (2, 1, 1))
        again = self.upload(GOOD_CSV)
        self.assertEqual((again["rows_new"], again["rows_duplicate"]), (0, 2))
        self.assertTrue(any("already imported" in w for w in again["warnings"]))
        listed = self.ok_call("GET", "imports", query={"account": str(self.acct["id"])})["imports"]
        self.assertEqual([(i["file_name"], i["rows_new"], i["undone_at"]) for i in listed], [("march.csv", 2, None), ("march.csv", 0, None)])
        undone = self.ok_call("POST", f"imports/{again['import_id']}/undo", {})
        self.assertEqual((undone["removed"], undone["import_id"]), (0, again["import_id"]))
        undone = self.ok_call("POST", f"imports/{result['import_id']}/undo")
        self.assertEqual(undone["removed"], 2)
        self.assertEqual(self.count(), 4)
        self.assertIn("already undone", self.refused(409, "POST", f"imports/{result['import_id']}/undo", {}))
        self.refused(404, "POST", "imports/999/undo", {})
        self.refused(422, "GET", "imports", query={"account": "999"})

    def test_undo_asks_for_force_before_losing_work_done_by_hand(self) -> None:
        result = self.upload(GOOD_CSV)
        row = self.ok_call("GET", "transactions", query={"period": "2025-03", "limit": "1"})["transactions"][0]
        self.ok_call("POST", f"transactions/{row['id']}/category", {"category": "6150"})
        message = self.refused(409, "POST", f"imports/{result['import_id']}/undo", {"force": False})
        self.assertIn(message, self.fails("imports", "undo", str(result["import_id"])))
        self.assertEqual(self.count(), 6)
        self.assertEqual(self.ok_call("POST", f"imports/{result['import_id']}/undo", {"force": True})["manual_lost"], 1)
        self.assertEqual(self.count(), 4)

    def test_unreadable_rows_stop_the_import_unless_skipped(self) -> None:
        bad = GOOD_CSV + b"13/45/2025,NOWHERE,-1.00\n"
        message = self.refused(422, "POST", "imports", query={"account": str(self.acct["id"]), "filename": "bad.csv"}, upload=bad)
        self.assertIn("Nothing was imported", message)
        dry = self.upload(bad, status=200, dry_run="true")
        self.assertEqual(dry["rows_bad"], 1)
        self.assertEqual(len(dry["bad_rows"]), 1)
        skipped = self.upload(bad, skip_bad="true")
        self.assertEqual((skipped["rows_new"], skipped["rows_bad"]), (2, 1))

    def test_a_file_that_is_not_an_export_is_refused_by_name(self) -> None:
        message = self.refused(422, "POST", "imports", query={"account": str(self.acct["id"]), "filename": "page.csv"}, upload=b"<!DOCTYPE html><html>Sign in</html>")
        self.assertIn("web page", message)
        self.assertEqual(self.count(), 4)

    def test_an_ofx_file_needs_no_mapping(self) -> None:
        savings = self.account(self.biz, "A Savings", kind="savings")
        books.set_account_mapping(self.conn, savings, "")
        data = (REPO_ROOT / "samples" / "layouts" / "example-bank.qfx").read_bytes()
        result = self.ok_call("POST", "imports", query={"account": str(savings["id"]), "filename": "example-bank.qfx"}, upload=data, status=201)
        self.assertGreater(result["rows_new"], 0)
        self.assertEqual(result["file_name"], "example-bank.qfx")

    def test_the_upload_s_name_cannot_leave_its_folder(self) -> None:
        result = self.upload(GOOD_CSV, filename="..\\..\\evil/../march?.csv")
        self.assertEqual(result["file_name"], "march_.csv")
        self.refused(400, "POST", "imports", query={"account": str(self.acct["id"]), "filename": "../.."}, upload=GOOD_CSV)

    def test_upload_requests_refuse_plainly(self) -> None:
        self.assertIn("account", self.refused(400, "POST", "imports", query={"filename": "x.csv"}, upload=GOOD_CSV))
        self.refused(422, "POST", "imports", query={"account": "999", "filename": "x.csv"}, upload=GOOD_CSV)
        self.assertIn("filename", self.refused(400, "POST", "imports", query={"account": str(self.acct["id"])}, upload=GOOD_CSV))
        self.assertIn("empty", self.refused(400, "POST", "imports", query={"account": str(self.acct["id"]), "filename": "x.csv"}, upload=b""))
        status, data, _ = self.call("POST", "imports", {"rows": []}, query={"account": str(self.acct["id"]), "filename": "x.csv"})
        self.assertEqual(status, 400)
        self.assertIn("application/octet-stream", data["error"]["message"])
        unmapped = self.account(self.biz, "B Bank")
        books.set_account_mapping(self.conn, unmapped, "")
        self.assertIn("no column mapping", self.refused(422, "POST", "imports", query={"account": str(unmapped["id"]), "filename": "x.csv"}, upload=GOOD_CSV))
        self.assertEqual(self.count(), 4)


class Rules(ApiCase):
    def test_add_a_rule_and_it_books_the_queue(self) -> None:
        added = self.ok_call("POST", "rules", {"match": "contains", "pattern": "BLUE FERN", "category": "6150"}, status=201)
        self.assertEqual(added["categorized"], 2)
        rule = added["rule"]
        self.assertEqual((rule["match"], rule["pattern"], rule["category"]["code"], rule["priority"], rule["rows"]), ("contains", "BLUE FERN", "6150", 100, 2))
        limited = self.ok_call(
            "POST", "rules",
            {"business_id": self.biz["id"], "match": "starts", "pattern": "REDWOOD", "category": "6105", "priority": 10,
             "direction": "out", "min": "100.00", "max": "2,000.00", "account_id": self.acct["id"]},
            status=201,
        )["rule"]
        self.assertEqual((limited["min_cents"], limited["max"], limited["direction"], limited["account"]), (10000, "2,000.00", "out", "A Checking"))
        listed = self.ok_call("GET", "rules")["rules"]
        self.assertEqual([r["id"] for r in listed], [limited["id"], rule["id"]])
        self.assertEqual(listed, self.ok_call("GET", "rules", query={"business": str(self.biz["id"])})["rules"])

    def test_adding_refuses_what_the_cli_refuses(self) -> None:
        bad = self.refused(422, "POST", "rules", {"match": "regex", "pattern": "(a+)+", "category": "6150"})
        self.assertIn(bad, self.fails("rules", "add", "--regex", "(a+)+", "--category", "6150"))
        self.assertIn("match type", self.refused(422, "POST", "rules", {"match": "fuzzy", "pattern": "X", "category": "6150"}))
        self.assertIn("direction", self.refused(422, "POST", "rules", {"match": "contains", "pattern": "X", "category": "6150", "direction": "up"}))
        self.assertIn("above the maximum", self.refused(422, "POST", "rules", {"match": "contains", "pattern": "X", "category": "6150", "min": "5", "max": "1"}))
        self.refused(400, "POST", "rules", {"match": "contains", "category": "6150"})
        other = self.account(self.business("Business B"), "B Bank")
        self.assertIn("another business", self.refused(422, "POST", "rules", {"business_id": self.biz["id"], "match": "contains", "pattern": "X", "category": "6150", "account_id": other["id"]}))
        self.assertIn("business_id", self.refused(400, "POST", "rules", {"match": "contains", "pattern": "X", "category": "6150"}))
        self.refused(422, "POST", "rules", {"business_id": 999, "match": "contains", "pattern": "X", "category": "6150"})
        self.assertEqual(rules.list_rules(self.conn, self.biz["id"]), [])

    def test_delete_a_rule_and_its_rows_stay_booked(self) -> None:
        rule = self.ok_call("POST", "rules", {"match": "contains", "pattern": "BLUE FERN", "category": "6150"}, status=201)["rule"]
        self.assertEqual(self.ok_call("DELETE", f"rules/{rule['id']}"), {"deleted": rule["id"]})
        self.assertEqual(self.ok_call("GET", "rules")["rules"], [])
        self.assertEqual(ledger.get_transaction(self.conn, self.cafe)["category_code"], "6150")
        self.assertIn("no rule", self.refused(404, "DELETE", f"rules/{rule['id']}"))

    def test_test_a_description(self) -> None:
        self.ok_call("POST", "rules", {"match": "contains", "pattern": "LEDGERLY", "category": "6200", "direction": "out"}, status=201)
        hit = self.ok_call("POST", "rules/test", {"description": "LEDGERLY LABS SUBSCRIPTION"})
        self.assertEqual((hit["merchant"], hit["category"]["code"], hit["rule"]["pattern"], hit["tested_amount"]), ("LEDGERLY LABS SUBSCRIPTION", "6200", "LEDGERLY", "-1.00"))
        miss = self.ok_call("POST", "rules/test", {"description": "LEDGERLY REFUND", "amount": "5.00", "account_id": self.acct["id"]})
        self.assertIsNone(miss["rule"])
        self.assertIsNone(miss["category"])
        self.refused(400, "POST", "rules/test", {})

    def test_apply_and_recheck(self) -> None:
        rules.add_rule(self.conn, self.biz["id"], "contains", "BLUE FERN", self.category(self.biz, "6150"))
        applied = self.ok_call("POST", "rules/apply", {})["results"]
        self.assertEqual([(r["business"], r["categorized"]) for r in applied], [("Business A", 2)])
        self.conn.execute("DELETE FROM rules")
        rechecked = self.ok_call("POST", "rules/apply", {"business_id": self.biz["id"], "recheck": True})["results"][0]
        self.assertEqual(rechecked["returned_to_review"], 2)
        self.refused(400, "POST", "rules/apply", {"recheck": "yes"})


class Reconciling(ApiCase):
    def run_january(self, end: str) -> dict:
        body = {"account_id": self.acct["id"], "from": "2025-01-01", "to": "2025-01-31", "begin": "1000.00", "end": end}
        return self.ok_call("POST", "reconciliations", body)["reconciliation"]

    def test_run_finish_and_reopen(self) -> None:
        report = self.run_january("990.00")
        self.assertTrue(report["balanced"])
        self.assertEqual((report["difference_cents"], report["rows"], report["status"]), (0, 2, "open"))
        self.assertEqual((report["money_out"], report["books_end_printed"]), ("10.00", "990.00"))
        finished = self.ok_call("POST", f"reconciliations/{report['id']}/finish")["reconciliation"]
        self.assertEqual(finished["status"], "finished")
        self.assertEqual(ledger.get_transaction(self.conn, self.cafe)["reconciliation_id"], report["id"])
        self.assertIn("already finished", self.refused(409, "POST", f"reconciliations/{report['id']}/finish"))
        self.assertIn("overlaps", self.refused(409, "POST", "reconciliations", {"account_id": self.acct["id"], "from": "2025-01-15", "to": "2025-02-15", "begin": "1.00", "end": "1.00"}))
        self.assertEqual(self.ok_call("GET", f"accounts/{self.acct['id']}")["reconciled_through"], "2025-01-31")
        reopened = self.ok_call("POST", f"reconciliations/{report['id']}/reopen", {})["reconciliation"]
        self.assertEqual(reopened["status"], "open")
        self.assertIn("not finished", self.refused(409, "POST", f"reconciliations/{report['id']}/reopen"))

    def test_a_difference_is_explained_and_cannot_be_finished(self) -> None:
        # The statement lacks the 4.50 cafe row, so it shows 4.50 less money out than the books.
        report = self.run_january("994.50")
        self.assertFalse(report["balanced"])
        self.assertEqual(report["difference_cents"], 450)
        self.assertIn("more money in", report["difference_says"])
        self.assertTrue(any(f"Transaction {self.cafe} " in hint and "equals the difference" in hint for hint in report["hints"]))
        message = self.refused(409, "POST", f"reconciliations/{report['id']}/finish")
        self.assertIn(message, self.fails("reconcile", "finish", str(report["id"])))
        listed = self.ok_call("GET", "reconciliations", query={"account": str(self.acct["id"])})["reconciliations"]
        self.assertEqual([(r["id"], r["balanced"], r["difference"]) for r in listed], [(report["id"], False, "4.50")])
        again = self.run_january("990.00")
        self.assertEqual(again["id"], report["id"])
        self.assertTrue(again["balanced"])

    def test_a_card_reads_as_what_is_owed(self) -> None:
        card = self.account(self.biz, "A Card", kind="credit_card", opening=-42500)
        ledger.add_manual(self.conn, card, date(2025, 1, 9), -1000, "STAPLE HUT 1")
        body = {"account_id": card["id"], "from": "2025-01-01", "to": "2025-01-31", "begin": "425.00", "end": "435.00"}
        report = self.ok_call("POST", "reconciliations", body)["reconciliation"]
        self.assertTrue(report["balanced"])
        self.assertTrue(report["printed_as_owed"])
        self.assertEqual((report["statement_end_cents"], report["statement_end_printed"]), (-43500, "435.00"))

    def test_running_refuses_plainly(self) -> None:
        base = {"account_id": self.acct["id"], "from": "2025-01-31", "to": "2025-01-01", "begin": "1.00", "end": "1.00"}
        self.assertIn("ends before it starts", self.refused(422, "POST", "reconciliations", base))
        self.assertIn("not a date", self.refused(422, "POST", "reconciliations", dict(base, to="soon")))
        self.assertIn("not an amount", self.refused(422, "POST", "reconciliations", dict(base, to="2025-02-01", end="lots")))
        self.refused(400, "POST", "reconciliations", {"account_id": self.acct["id"]})
        self.refused(422, "POST", "reconciliations", dict(base, account_id=999))
        self.refused(404, "GET", "reconciliations/999")
        self.refused(404, "POST", "reconciliations/999/finish")
        self.refused(404, "POST", "reconciliations/999/reopen")
        self.refused(400, "POST", "reconciliations/999/finish", {"now": True})

    def test_rows_added_after_finishing_are_listed(self) -> None:
        report = self.run_january("990.00")
        self.ok_call("POST", f"reconciliations/{report['id']}/finish")
        self.ok_call("POST", "transactions", {"account_id": self.acct["id"], "date": "2025-01-20", "amount": "-1.00", "description": "LATE ROW"}, status=201)
        shown = self.ok_call("GET", f"reconciliations/{report['id']}")["reconciliation"]
        self.assertEqual([row["description"] for row in shown["rows_added_after_finish"]], ["LATE ROW"])
        self.assertFalse(shown["balanced"])
