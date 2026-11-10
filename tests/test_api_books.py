"""The API for history, tags, payees, budgets, reports, settings and the fuller rules, through a real server."""
from __future__ import annotations

import json
import os
from unittest import mock

from solo_books import history, ledger
from tests.test_api import ApiCase


class History(ApiCase):
    def test_list_show_and_revert(self) -> None:
        self.ok_call("POST", f"transactions/{self.cafe}/category", {"category": "6150"})
        changes = self.ok_call("GET", "history", query={"kind": "categorize"})["changes"]
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual((change["source"], change["rows"], change["revertible"]), ("web", 1, True))
        self.assertEqual(self.ok_call("GET", "history", query={"row": str(self.cafe)})["changes"][0]["id"], change["id"])
        shown = self.ok_call("GET", f"history/{change['id']}")["change"]
        self.assertEqual(shown["row_changes"][0]["transaction_id"], self.cafe)
        self.assertIn("in the queue", shown["row_changes"][0]["before_text"])
        back = self.ok_call("POST", f"history/{change['id']}/revert")
        self.assertEqual(back["reverted"], change["id"])
        self.assertEqual(back["change"]["kind"], "revert")
        self.assertIsNone(ledger.get_transaction(self.conn, self.cafe)["category_id"])
        self.assertIn("already taken back", self.refused(409, "POST", f"history/{change['id']}/revert"))
        self.assertIn("no change 999", self.refused(404, "GET", "history/999"))
        self.refused(404, "POST", "history/999/revert")
        self.refused(400, "GET", "history", query={"colour": "red"})
        self.refused(422, "GET", "history", query={"kind": "forecast"})

    def test_every_write_from_the_api_is_recorded_as_web(self) -> None:
        self.ok_call("POST", f"transactions/{self.cafe}/note", {"note": "lunch"})
        self.ok_call("POST", "tags/add", {"tag": "Spring", "ids": [self.cafe]})
        self.ok_call("POST", "payees/rename", {"payee": "REDWOOD PLAZA RENT", "name": "Redwood"})
        self.ok_call("POST", "budgets", {"category": "6150", "year": 2025, "yearly": "100.00"})
        self.ok_call("POST", "rules", {"conditions": [{"kind": "contains", "text": "BLUE FERN"}], "category": "6150"}, status=201)
        sources = {row["kind"]: row["source"] for row in history.list_entries(self.conn)}
        for kind in ("note", "tag-add", "payee-rename", "budget-set", "rule-add"):
            self.assertEqual(sources[kind], "web", kind)
        self.assertEqual(history.current_source(), "", "a request leaves nothing behind for the next command")


class Tags(ApiCase):
    def test_add_list_and_remove(self) -> None:
        added = self.ok_call("POST", "tags/add", {"tag": "Harbor Lane", "ids": [self.cafe, self.rent]})
        self.assertEqual((added["tag"], added["added"], added["created"]), ("Harbor Lane", 2, True))
        listed = self.ok_call("GET", "tags")["tags"]
        self.assertEqual([(t["name"], t["rows"], t["money_out"]) for t in listed], [("Harbor Lane", 2, "854.50")])
        rows = self.ok_call("GET", "transactions", query={"limit": "10"})["transactions"]
        self.assertEqual({r["id"]: r["tags"] for r in rows}[self.cafe], ["Harbor Lane"])
        self.assertEqual(self.ok_call("GET", f"transactions/{self.cafe}")["tags"], ["Harbor Lane"])
        removed = self.ok_call("POST", "tags/remove", {"tag": "harbor lane", "ids": [self.cafe]})
        self.assertEqual(removed["removed"], 1)
        self.assertEqual(self.ok_call("GET", f"transactions/{self.cafe}")["tags"], [])
        self.refused(400, "POST", "tags/add", {"tag": "X", "ids": []})
        self.refused(400, "POST", "tags/add", {"tag": "X", "ids": ["1"]})
        self.assertIn("no transaction 999", self.refused(422, "POST", "tags/add", {"tag": "X", "ids": [999]}))
        self.refused(422, "POST", "tags/add", {"tag": "a\tb", "ids": [self.cafe]})


class Payees(ApiCase):
    def test_rename_merge_unmerge_and_flag(self) -> None:
        listed = self.ok_call("GET", "payees", query={"year": "2025"})["payees"]
        self.assertIn("REDWOOD PLAZA RENT", [p["name"] for p in listed])
        renamed = self.ok_call("POST", "payees/rename", {"payee": "REDWOOD PLAZA RENT", "name": "Redwood Plaza"})["payee"]
        self.assertEqual((renamed["name"], renamed["renamed"]), ("Redwood Plaza", True))
        flagged = self.ok_call("POST", "payees/contractor", {"payee": "Redwood Plaza", "contractor": True})["payee"]
        self.assertTrue(flagged["contractor"])
        cafe = [p for p in self.ok_call("GET", "payees")["payees"] if "BLUE FERN" in p["name"]][0]
        merged = self.ok_call("POST", "payees/merge", {"payee": cafe["key"], "into": "Redwood Plaza"})
        self.assertEqual(sorted(merged["payee"]["spellings"]), sorted(["REDWOOD PLAZA RENT", cafe["key"]]))
        back = self.ok_call("POST", "payees/unmerge", {"payee": cafe["key"]})
        self.assertEqual(back["apart_from"], "Redwood Plaza")
        self.assertIn("nothing to take out", self.refused(422, "POST", "payees/unmerge", {"payee": cafe["key"]}))
        self.refused(400, "POST", "payees/contractor", {"payee": "Redwood Plaza"})
        self.refused(400, "POST", "payees/contractor", {"payee": "Redwood Plaza", "contractor": "yes"})
        self.assertIn("no payee", self.refused(422, "POST", "payees/rename", {"payee": "NOBODY", "name": "X"}))
        self.refused(400, "GET", "payees", query={"year": "1700"})


class Budgets(ApiCase):
    def test_set_list_and_delete(self) -> None:
        saved = self.ok_call("POST", "budgets", {"category": "6150", "year": 2025, "yearly": "1,200.00"})
        self.assertEqual((saved["budget"]["year_total"], saved["budget"]["entered_as"]), ("1,200.00", "yearly"))
        month = self.ok_call("POST", "budgets", {"category": "6150", "year": 2025, "monthly": "50.00", "month": 3})
        self.assertTrue(month["spread"])
        self.assertEqual(month["budget"]["months"][2]["amount"], "50.00")
        listed = self.ok_call("GET", "budgets", query={"year": "2025"})["budgets"]
        self.assertEqual([b["code"] for b in listed], ["6150"])
        deleted = self.ok_call("POST", "budgets/delete", {"category": "6150", "year": 2025})
        self.assertEqual(deleted["deleted"]["code"], "6150")
        self.assertEqual(self.ok_call("GET", "budgets")["budgets"], [])
        self.refused(400, "POST", "budgets", {"category": "6150", "year": 2025})
        self.refused(400, "POST", "budgets", {"category": "6150", "year": 2025, "yearly": "1.00", "monthly": "1.00"})
        self.refused(400, "POST", "budgets", {"category": "6150", "year": 2025, "monthly": "1.00", "month": 13})
        self.refused(400, "POST", "budgets", {"category": "6150", "year": 1700, "yearly": "1.00"})
        self.refused(422, "POST", "budgets", {"category": "9999", "year": 2025, "yearly": "1.00"})


QUERIES = {
    "pnl": {"period": "2025", "by": "quarter"},
    "balances": {"period": "2025"},
    "schedule-c": {"year": "2025"},
    "cashflow": {"period": "2025"},
    "trend": {"years": "2025"},
    "categories": {"period": "2025-Q1"},
    "budget": {"period": "2025"},
    "payees": {"year": "2025", "limit": "3"},
    "contractors": {"year": "2025", "threshold": "100.00"},
}


def _cli_args(query: dict) -> list:
    found = []
    for name, value in query.items():
        found.extend(["--" + name.replace("_", "-"), value])
    return found


class Reports(ApiCase):
    def test_every_report_answers_as_the_command_line_writes_it(self) -> None:
        for kind, query in QUERIES.items():
            with self.subTest(kind=kind):
                answer = self.ok_call("GET", f"reports/{kind}", query=query)
                written = json.loads(self.ok("report", kind, *_cli_args(query), "--format", "json"))
                self.assertEqual(answer, written)
                status, body, headers = self.raw(
                    "GET", f"/api/v1/reports/{kind}?" + "&".join(f"{k}={v}" for k, v in {**query, "format": "csv"}.items()),
                    headers={"X-Solo-Books-Token": self.server.token},
                )
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith("text/csv"))
                self.assertIn("attachment; filename=", headers["Content-Disposition"])
                path = self.dir / f"{kind}.csv"
                self.ok("report", kind, *_cli_args(query), "--format", "csv", "-o", str(path))
                self.assertEqual(body, path.read_bytes())
                page = self.ok_call("GET", f"reports/{kind}/document", query=query)
                self.assertEqual(page["report"], kind)
                self.assertTrue(page["sections"])

    def test_tags_html_and_refusals(self) -> None:
        self.ok_call("POST", "tags/add", {"tag": "Spring", "ids": [self.cafe]})
        tagged = self.ok_call("GET", "reports/pnl", query={"period": "2025", "tag": "spring"})
        self.assertEqual(tagged["tag"], "Spring")
        status, body, headers = self.raw("GET", "/api/v1/reports/budget?period=2025&format=html", headers={"X-Solo-Books-Token": self.server.token})
        self.assertEqual(status, 200)
        self.assertIn(b"<table", body)
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="budget-2025.html"')
        self.refused(400, "GET", "reports/balances", query={"period": "2025", "tag": "Spring"})
        self.assertIn("needs a period", self.refused(422, "GET", "reports/pnl"))
        self.refused(400, "GET", "reports/pnl", query={"period": "2025", "format": "pdf"})
        self.refused(422, "GET", "reports/pnl", query={"period": "2025", "by": "week"})
        self.refused(404, "GET", "reports/forecast")
        listed = self.ok_call("GET", "reports")
        self.assertEqual(len(listed["reports"]), 9)
        self.assertIn("markdown", listed["formats"])

    def test_dates_follow_the_settings(self) -> None:
        with mock.patch.dict(os.environ, {"SOLO_BOOKS_DATE_FORMAT": "us"}):
            page = self.ok_call("GET", "reports/pnl/document", query={"period": "2025"})
            answer = self.ok_call("GET", "reports/pnl", query={"period": "2025"})
        self.assertIn("(01/01/2025 to 12/31/2025)", page["sections"][0]["intro"][0])
        self.assertEqual(answer["period"]["start"], "2025-01-01")


class Config(ApiCase):
    def test_settings_are_shown_and_cannot_be_changed(self) -> None:
        with mock.patch.dict(os.environ, {"SOLO_BOOKS_REPORT_FORMAT": "markdown"}):
            shown = self.ok_call("GET", "config")
        settings = {entry["name"]: entry for entry in shown["settings"]}
        self.assertEqual((settings["report_format"]["value"], settings["report_format"]["from"]), ("markdown", "environment SOLO_BOOKS_REPORT_FORMAT"))
        self.assertEqual(settings["date_format"]["from"], "default")
        self.assertIsNone(shown["profile"])
        self.refused(405, "POST", "config", {})
        self.refused(400, "GET", "config", query={"name": "x"})


class FullerRules(ApiCase):
    def test_conditions_enable_disable_move_and_explain(self) -> None:
        first = self.ok_call(
            "POST", "rules",
            {"conditions": [{"kind": "contains", "text": "BLUE FERN"}, {"kind": "amount_at_most", "amount": "5.00"}],
             "category": "6150", "note": "coffee"},
            status=201,
        )
        self.assertEqual(first["categorized"], 1)
        rule = first["rule"]
        self.assertEqual(
            [c["kind"] for c in rule["conditions"]], ["contains", "amount_at_most"],
        )
        self.assertEqual((rule["note"], rule["enabled"], rule["place"]), ("coffee", True, 1))
        self.assertIn("size is at most 5.00", rule["said"])
        second = self.ok_call(
            "POST", "rules", {"conditions": [{"kind": "account", "account": "A Checking"}, {"kind": "direction", "direction": "out"}], "category": "6080"},
            status=201,
        )
        self.assertGreaterEqual(second["categorized"], 1, "a new rule books the queue straight away")
        second = second["rule"]
        moved = self.ok_call("POST", f"rules/{second['id']}/move", {"to": 1})
        self.assertEqual(moved["place"], 1)
        self.assertEqual([r["id"] for r in moved["rules"]], [second["id"], rule["id"]])
        off = self.ok_call("POST", f"rules/{second['id']}/disable")["rule"]
        self.assertFalse(off["enabled"])
        explained = self.ok_call("GET", "rules/explain", query={"transaction": str(self.cafe2)})
        verdicts = {t["rule_id"]: t["verdict"] for t in explained["trials"]}
        self.assertIn("disabled", verdicts[second["id"]])
        self.assertIsNone(explained["winner"])
        on = self.ok_call("POST", f"rules/{second['id']}/enable")
        self.assertTrue(on["rule"]["enabled"])
        self.assertEqual(on["categorized"], 0, "the rows it booked before it was turned off stay booked")
        self.assertEqual(self.ok_call("GET", "rules/explain", query={"transaction": str(self.cafe2)})["winner"], second["id"])
        self.assertIn("unknown condition kind", self.refused(422, "POST", "rules", {"conditions": [{"kind": "colour"}], "category": "6150"}))
        self.assertIn("only one", self.refused(422, "POST", "rules", {"conditions": [{"kind": "contains", "text": "A"}, {"kind": "exact", "text": "B"}], "category": "6150"}))
        self.refused(400, "POST", "rules", {"category": "6150"})
        self.refused(400, "GET", "rules/explain")
        self.refused(404, "GET", "rules/explain", query={"transaction": "999"})
        self.refused(404, "POST", "rules/999/enable")
        self.assertIn("place must be", self.refused(422, "POST", f"rules/{rule['id']}/move", {"to": 9}))
        kinds = [row["kind"] for row in history.list_entries(self.conn, kind="rule")]
        for kind in ("rule-add", "rule-move", "rule-disable", "rule-enable"):
            self.assertIn(kind, kinds)


class NewPages(ApiCase):
    def test_each_page_opens_with_the_key(self) -> None:
        for path in ("/history", "/payees", "/budgets", "/reports", "/rules", "/transactions"):
            with self.subTest(path=path):
                status, body, headers = self.raw("GET", f"{path}?key={self.server.token}")
                self.assertEqual(status, 200)
                self.assertIn(b'href="/reports?key=', body)
                self.assertIn("nonce-", headers["Content-Security-Policy"])
                self.assertEqual(self.raw("GET", path)[0], 403)
