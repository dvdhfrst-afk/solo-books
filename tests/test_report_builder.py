"""Every report through every format, limited to a tag, and shown with the date and format settings."""
from __future__ import annotations

import json
import os
from unittest import mock

from solo_books import books, formats, report_builder
from solo_books.errors import BooksError
from tests.helpers import BooksCase, build_sample_books, parse_csv

ASKED = {
    "pnl": ["--period", "2025"],
    "balances": ["--period", "2025"],
    "schedule-c": ["--year", "2025"],
    "cashflow": ["--period", "2025"],
    "trend": ["--years", "2025"],
    "categories": ["--period", "2025"],
    "budget": ["--period", "2025"],
    "payees": ["--year", "2025"],
    "contractors": ["--year", "2025"],
}


class SampleReports(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        build_sample_books(self)
        self.ok("budget", "set", "--business", "Business A", "--year", "2025", "--category", "6150", "--yearly", "1200.00")
        self.ok("payee", "set", "PIXEL FOUNDRY CONTRACTOR", "--business", "Business A", "--contractor", "yes")
        rows = self.conn.execute(
            "SELECT t.id, t.amount_cents FROM transactions t JOIN accounts a ON a.id = t.account_id"
            " JOIN categories c ON c.id = t.category_id"
            " WHERE a.name = 'A Checking' AND c.kind = 'expense' ORDER BY t.id LIMIT 2"
        ).fetchall()
        self.tagged = [row["id"] for row in rows]
        self.tagged_out = -sum(row["amount_cents"] for row in rows)
        self.ok("tag", "add", "Spring", *map(str, self.tagged))

    def report(self, kind: str, *more: str) -> str:
        return self.ok("report", kind, *ASKED[kind], *more)

    def as_json(self, kind: str, *more: str) -> dict:
        return json.loads(self.report(kind, "--format", "json", *more))

    def test_every_report_in_every_format(self) -> None:
        for kind in ASKED:
            for name in formats.names():
                with self.subTest(kind=kind, format=name):
                    out = self.report(kind, "--format", name)
                    self.assertTrue(out.strip())
                    if name == "json":
                        found = json.loads(out)
                        self.assertEqual((found["report"], found["format_version"]), (kind, 1))
                        self.assertTrue(found["businesses"])
                    elif name == "html":
                        self.assertIn("<table", out)
                        self.assertNotIn("<script", out)
                    elif name == "markdown":
                        self.assertTrue(out.startswith("# "))
                    elif name == "csv":
                        self.assertTrue(parse_csv(out))

    def test_payees_json(self) -> None:
        found = self.as_json("payees", "--business", "Business A", "--limit", "3")
        self.assertEqual((found["limit"], found["tag"]), (3, None))
        part = found["businesses"][0]
        self.assertLessEqual(len(part["money_out"]), 3)
        first = part["money_out"][0]
        self.assertEqual(first["rank"], 1)
        self.assertEqual(set(first), {
            "rank", "payee", "spellings", "contractor", "rows", "money_out", "money_in", "net_paid", "paid_by_card",
        })
        self.assertEqual(first["net_paid"]["cents"], first["money_out"]["cents"] - first["money_in"]["cents"])

    def test_contractors_json(self) -> None:
        found = self.as_json("contractors", "--business", "Business A", "--threshold", "1.00")
        self.assertEqual(found["tax_year"], 2025)
        part = found["businesses"][0]
        self.assertEqual((part["flagged"], part["threshold"]["cents"]), (1, 100))
        names = [entry["payee"] for entry in part["at_or_over"] + part["under"]]
        self.assertEqual(names, ["PIXEL FOUNDRY CONTRACTOR"])

    def test_budget_json(self) -> None:
        part = self.as_json("budget", "--business", "Business A")["businesses"][0]
        expenses = [s for s in part["sections"] if s["section"] == "expense"][0]
        meals = [c for c in expenses["categories"] if c["code"] == "6150"][0]
        self.assertEqual(meals["budget"]["cents"], 120000)
        self.assertEqual(meals["difference"]["cents"], meals["actual"]["cents"] - 120000)
        table = self.report("budget", "--business", "Business A")
        self.assertIn("Budget: Business A", table)

    def test_tag_limits_every_report_that_adds_rows(self) -> None:
        tag = ("--business", "Business A", "--tag", "spring")
        pnl = self.as_json("pnl", *tag)
        self.assertEqual(pnl["tag"], "Spring")
        self.assertEqual(pnl["businesses"][0]["totals"]["total_expenses"]["total"]["cents"], self.tagged_out)
        cash = self.as_json("cashflow", *tag)["businesses"][0]
        self.assertEqual(cash["all_accounts"]["money_out"]["cents"], self.tagged_out)
        self.assertEqual(cash["all_accounts"]["money_in"]["cents"], 0)
        cats = self.as_json("categories", *tag)["businesses"][0]
        self.assertEqual(sum(c["rows"] for c in cats["categories"]), 2)
        trend = self.as_json("trend", *tag)["businesses"][0]
        self.assertEqual(trend["totals"]["total_expenses"]["values"][0]["cents"], self.tagged_out)
        budget = self.as_json("budget", *tag)["businesses"][0]
        spent = [s for s in budget["sections"] if s["section"] == "expense"][0]["total"]["actual"]["cents"]
        self.assertEqual(spent, self.tagged_out)
        payees = self.as_json("payees", *tag)["businesses"][0]
        self.assertEqual(sum(e["rows"] for e in payees["money_out"]), 2)
        for kind in report_builder.TAGGABLE:
            with self.subTest(kind=kind):
                self.assertIn("Only rows tagged Spring.", self.report(kind, *tag))
                self.assertIn("Only rows tagged Spring.", self.report(kind, *tag, "--format", "csv"))
                self.assertIn("Only rows tagged Spring.", self.report(kind, *tag, "--format", "html"))

    def test_a_tag_on_every_business_skips_those_without_it(self) -> None:
        found = self.as_json("cashflow", "--tag", "Spring")
        self.assertEqual([b["business"] for b in found["businesses"]], ["Business A"])
        self.assertIn("no tag 'Autumn'", self.fails("report", "payees", "--year", "2025", "--tag", "Autumn"))

    def test_reports_that_do_not_add_rows_take_no_tag(self) -> None:
        for kind in ("balances", "schedule-c", "contractors"):
            with self.subTest(kind=kind):
                code, _, err = self.run_cli("report", kind, *ASKED[kind], "--tag", "Spring")
                self.assertEqual(code, 2)
                with self.assertRaises(BooksError):
                    report_builder.build(self.conn, kind, books.list_businesses(self.conn), period="2025", year=2025, tag="Spring")

    def test_the_builder_says_what_is_missing(self) -> None:
        businesses = books.list_businesses(self.conn)
        for kind, needs in (("pnl", "needs a period"), ("payees", "needs a year"), ("trend", "needs years")):
            with self.assertRaises(BooksError) as caught:
                report_builder.build(self.conn, kind, businesses)
            self.assertIn(needs, str(caught.exception))
        with self.assertRaises(BooksError):
            report_builder.build(self.conn, "pnl", businesses, period="2025", by="week")
        with self.assertRaises(BooksError):
            report_builder.build(self.conn, "forecast", businesses, period="2025")

    def test_the_report_format_setting_takes_every_format(self) -> None:
        for name in formats.names():
            with self.subTest(format=name), mock.patch.dict(os.environ, {"SOLO_BOOKS_REPORT_FORMAT": name}):
                expected = self.report("payees", "--format", name)
                self.assertEqual(self.report("payees"), expected)
        with mock.patch.dict(os.environ, {"SOLO_BOOKS_REPORT_FORMAT": "json"}):
            self.assertTrue(self.report("budget", "--format", "table").startswith("Budget: "), "the flag wins")

    def test_date_setting_shows_dates_for_people_and_keeps_files_iso(self) -> None:
        with mock.patch.dict(os.environ, {"SOLO_BOOKS_DATE_FORMAT": "us"}):
            for kind in ("pnl", "balances", "cashflow", "categories", "budget"):
                with self.subTest(kind=kind):
                    self.assertIn("(01/01/2025 to 12/31/2025)", self.report(kind))
                    self.assertIn("(01/01/2025 to 12/31/2025)", self.report(kind, "--format", "html"))
                    self.assertIn("01/01/2025 to 12/31/2025", self.report(kind, "--format", "markdown"))
                    for machine in ("json", "csv"):
                        out = self.report(kind, "--format", machine)
                        self.assertIn("2025-01-01", out)
                        self.assertNotIn("01/01/2025", out)
        self.assertIn("(2025-01-01 to 2025-12-31)", self.report("pnl"))
