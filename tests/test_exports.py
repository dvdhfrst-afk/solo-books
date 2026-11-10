"""Every report format and export over the sample year, against samples/expected-2025.json, and the preparer summary.

The sample totals were worked out by the script that invented the rows. It never ran this program, so a match
here is an outside check on the new reports and exports, not this program agreeing with itself.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List

from solo_books import db
from solo_books.analysis import divide_cents
from solo_books.money import parse_cents
from tests.helpers import BooksCase, build_sample_books, build_worked_books, parse_csv, read_csv, sample_expected
from tests.test_archive import STAMP_LINE
from tests.test_journal import by_account, read_beancount, read_ledger, read_qif

BUSINESS_A = "Business A, after adding the missing parking row"
OPENING = {"A Checking": 1250000, "A Card": 42500, "B Checking": 300000}


def money_of(entry: dict) -> int:
    """The cents of a JSON amount, after checking the decimal string says the same."""
    assert parse_cents(entry["amount"]) == entry["cents"], entry
    return entry["cents"]


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cells: List[str] = []
        self.tags: List[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append(tag)

    def handle_data(self, data) -> None:
        if data.strip():
            self.cells.append(data.strip())


class SampleYearExports(BooksCase):
    """The README's sample year, built once and copied for each test."""

    template: Path

    @classmethod
    def setUpClass(cls) -> None:
        folder = Path(tempfile.mkdtemp(prefix="solo-books-sample-"))
        cls.addClassCleanup(shutil.rmtree, folder, ignore_errors=True)
        builder = BooksCase()
        builder.setUp()
        build_sample_books(builder)
        builder.conn.close()
        cls.template = folder / "sample.sqlite3"
        shutil.copyfile(builder.db_path, cls.template)
        builder.doCleanups()

    def setUp(self) -> None:
        super().setUp()
        self.conn.close()
        shutil.copyfile(self.template, self.db_path)
        self.conn = db.connect(self.db_path)
        self.addCleanup(self.conn.close)
        self.expected = sample_expected()
        self.wanted = {"Business A": self.expected[BUSINESS_A], "Business B": self.expected["Business B"]}
        self.ending = {name: parse_cents(value) for name, value in self.expected["ending_balances"].items()}

    def json_of(self, *command: str) -> dict:
        data = json.loads(self.ok(*command, "--format", "json"))
        self.assertEqual(data["format_version"], 1)
        return data

    def parts(self, data: dict) -> Dict[str, dict]:
        return {b["business"]: b for b in data["businesses"]}

    def kinds(self) -> Dict[str, str]:
        return {row["code"]: row["kind"] for row in self.conn.execute("SELECT code, kind FROM categories")}

    def test_profit_and_loss_and_schedule_c(self) -> None:
        for name, part in self.parts(self.json_of("report", "pnl", "--period", "2025")).items():
            wanted = self.wanted[name]
            totals = part["totals"]
            self.assertEqual(money_of(totals["total_income"]["total"]), parse_cents(wanted["income"]), name)
            self.assertEqual(money_of(totals["total_cogs"]["total"]), parse_cents(wanted["cogs"]), name)
            self.assertEqual(money_of(totals["total_expenses"]["total"]), parse_cents(wanted["expenses"]), name)
            self.assertEqual(money_of(totals["net"]["total"]), parse_cents(wanted["net"]), name)
        schedule = self.parts(self.json_of("report", "schedule-c", "--year", "2025"))
        a_lines = {line["line"]: money_of(line["amount"]) for line in schedule["Business A"]["lines"]}
        self.assertEqual(a_lines["31"], parse_cents(self.wanted["Business A"]["net"]))
        b_ties = schedule["Business B"]["ties_to_profit_and_loss"]
        self.assertEqual(money_of(b_ties["net_profit"]), parse_cents(self.wanted["Business B"]["net"]))
        # The ending inventory is 700.00 more than the beginning, which lowers cost of goods sold by as much.
        self.assertEqual(money_of(b_ties["line_31"]), parse_cents(self.wanted["Business B"]["net"]) + 70000)

    def test_balances_end_where_the_generator_says(self) -> None:
        parts = self.parts(self.json_of("report", "balances", "--period", "2025"))
        accounts = {a["name"]: a for part in parts.values() for a in part["accounts"]}
        self.assertEqual(money_of(accounts["A Checking"]["end"]), self.ending["A Checking"])
        self.assertEqual(accounts["A Card"]["shows"], "owed")
        self.assertEqual(money_of(accounts["A Card"]["end"]), self.ending["A Card owed"])
        self.assertEqual(money_of(accounts["B Checking"]["end"]), self.ending["B Checking"])
        self.assertEqual(money_of(parts["Business B"]["money_at_end"]), self.ending["B Checking"])
        self.assertEqual(
            money_of(parts["Business A"]["money_at_end"]), self.ending["A Checking"] - self.ending["A Card owed"]
        )

    def test_cash_flow_moves_each_account_from_opening_to_end(self) -> None:
        parts = self.parts(self.json_of("report", "cashflow", "--period", "2025", "--by", "quarter"))
        accounts = {a["name"]: a for part in parts.values() for a in part["accounts"]}
        self.assertEqual(money_of(accounts["A Checking"]["net"]), self.ending["A Checking"] - OPENING["A Checking"])
        self.assertEqual(money_of(accounts["B Checking"]["net"]), self.ending["B Checking"] - OPENING["B Checking"])
        # Paying down a card is money out of the business; the card's net is how much less is owed.
        self.assertEqual(money_of(accounts["A Card"]["net"]), OPENING["A Card"] - self.ending["A Card owed"])
        for name, part in parts.items():
            by_code = self.wanted[name]["by_code"]
            moved = {line["label"]: money_of(line["net"]) for line in part["what_moved_it"]}
            self.assertEqual(moved["Income and costs"], parse_cents(self.wanted[name]["net"]), name)
            self.assertEqual(moved["Owner money"], parse_cents(by_code["3100"]), name)
            self.assertEqual(moved.get("Transfers", 0), 0, name)
            self.assertEqual(sum(moved.values()), money_of(part["all_accounts"]["net"]), name)
            self.assertEqual(len(part["all_accounts"]["buckets"]), 4)
            self.assertIsNone(part["waiting_for_review"])
            quarters = sum(money_of(bucket["net"]) for bucket in part["all_accounts"]["buckets"])
            self.assertEqual(quarters, money_of(part["all_accounts"]["net"]))

    def test_categories_add_up_to_the_generator_by_code(self) -> None:
        kinds = self.kinds()
        parts = self.parts(self.json_of("report", "categories", "--period", "2025"))
        for name, part in parts.items():
            found = {c["code"]: c for c in part["categories"]}
            for code, amount in self.wanted[name]["by_code"].items():
                signed = parse_cents(amount)
                if signed == 0 and code not in found:
                    continue
                shown = money_of(found[code]["total"])
                reads = -shown if kinds[code] in ("cogs", "expense") else shown
                self.assertEqual(reads, signed, f"{name} {code}")
                self.assertEqual(money_of(found[code]["average"]), divide_cents(shown, found[code]["rows"]), f"{name} {code}")
            rows = sum(c["rows"] for c in part["categories"])
            self.assertEqual(
                rows,
                self.conn.execute(
                    "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id "
                    "JOIN businesses b ON b.id = a.business_id WHERE b.name = ? AND t.category_id IS NOT NULL",
                    (name,),
                ).fetchone()[0],
            )

    def test_trend_shows_the_year_against_an_empty_one(self) -> None:
        kinds = self.kinds()
        for name in ("Business A", "Business B"):
            data = self.json_of("report", "trend", "--business", name, "--years", "2024..2025")
            part = data["businesses"][0]
            self.assertEqual(part["years"], [2024, 2025])
            net = part["totals"]["net"]
            self.assertEqual([money_of(v) for v in net["values"]], [0, parse_cents(self.wanted[name]["net"])])
            self.assertEqual(money_of(net["change"]), parse_cents(self.wanted[name]["net"]))
            self.assertIsNone(net["change_percent"])
            for category in part["categories"]:
                code = category["code"]
                shown = money_of(category["values"][1])
                if kinds[code] in ("cogs", "expense"):
                    shown = -shown
                self.assertEqual(shown, parse_cents(self.wanted[name]["by_code"].get(code, "0.00")), f"{name} {code}")

    def test_html_and_markdown_carry_the_figures(self) -> None:
        net_a = "78,123.66"
        page = self.ok("report", "pnl", "--period", "2025", "--format", "html")
        parser = _Text()
        parser.feed(page)
        self.assertIn(net_a, parser.cells)
        self.assertNotIn("script", parser.tags)
        self.assertIn("Bookkeeping summary, not tax or accounting advice.", parser.cells)
        markdown = self.ok("report", "cashflow", "--period", "2025", "--format", "markdown")
        self.assertIn("| A Checking |", markdown)
        self.assertIn("48,893.61", markdown)
        for line in markdown.splitlines():
            if line.startswith("|"):
                self.assertTrue(line.endswith("|"), line)
        # Each row of a table has as many cells as its header.
        tables = re.findall(r"((?:^\|.*\|\n)+)", markdown, re.MULTILINE)
        self.assertTrue(tables)
        for table in tables:
            widths = {row.count(" | ") for row in table.strip().splitlines() if not set(row) <= set("|-: ")}
            self.assertEqual(len(widths), 1, table)

    def test_journals_tie_to_the_books_and_end_on_the_balances(self) -> None:
        net = {name: parse_cents(self.wanted[name]["net"]) for name in self.wanted}
        ledger_path = self.dir / "year.ledger"
        self.ok("export", "journal", "--format", "ledger", "--period", "2025", "-o", str(ledger_path))
        transactions = read_ledger(ledger_path.read_text(encoding="utf-8"))
        for txn in transactions:
            self.assertEqual(sum(cents for _, cents in txn["postings"]), 0, txn)
        totals = by_account(transactions)
        self.assertEqual(totals["Assets:Business A:A Checking"], self.ending["A Checking"])
        self.assertEqual(totals["Liabilities:Business A:A Card"], -self.ending["A Card owed"])
        self.assertEqual(totals["Assets:Business B:B Checking"], self.ending["B Checking"])
        for name in net:
            income = sum(v for k, v in totals.items() if k.startswith(f"Income:{name}:"))
            expenses = sum(v for k, v in totals.items() if k.startswith(f"Expenses:{name}:"))
            self.assertEqual(-(income + expenses), net[name], name)
        self.assertNotIn("Equity:Business A:Uncategorized", totals)

        bean_path = self.dir / "year.beancount"
        self.ok("export", "journal", "--format", "beancount", "--period", "2025", "-o", str(bean_path))
        entries, opened, checks = read_beancount(bean_path.read_text(encoding="utf-8"))
        bean = by_account(entries)
        for txn in entries:
            self.assertEqual(sum(cents for _, cents in txn["postings"]), 0, txn)
        for day, account, cents in checks:
            self.assertEqual(day, "2026-01-01")
            self.assertEqual(bean[account], cents, account)
        self.assertEqual(sorted(cents for _, _, cents in checks), sorted([
            self.ending["A Checking"], -self.ending["A Card owed"], self.ending["B Checking"],
        ]))

        qif_path = self.dir / "year.qif"
        self.ok("export", "journal", "--format", "qif", "--period", "2025", "--business", "Business B", "-o", str(qif_path))
        records = read_qif(qif_path.read_text(encoding="utf-8"))
        self.assertEqual(list(records), ["B Checking"])
        self.assertEqual(sum(parse_cents(r["T"]) for r in records["B Checking"]), self.ending["B Checking"])

        csv_path = self.dir / "year.csv"
        self.ok("export", "journal", "--format", "csv", "--period", "2025", "-o", str(csv_path))
        postings = read_csv(csv_path)
        self.assertEqual(sum(parse_cents(row["amount"]) for row in postings if row["entry"]), 0)
        self.assertEqual(
            len({row["transaction_id"] for row in postings if row["transaction_id"]}),
            self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
        )

    def test_the_archive_carries_the_year_whole(self) -> None:
        first = self.dir / "year.json"
        self.ok("export", "archive", "-o", str(first))
        copy = self.dir / "copy.sqlite3"
        self.ok("--db", str(copy), "import-archive", str(first))
        second = self.dir / "again.json"
        self.ok("--db", str(copy), "export", "archive", "-o", str(second))
        self.assertEqual(
            STAMP_LINE.sub("", first.read_text(encoding="utf-8")), STAMP_LINE.sub("", second.read_text(encoding="utf-8"))
        )
        for command in (("report", "schedule-c", "--year", "2025", "--format", "json"), ("report", "balances", "--period", "2025")):
            self.assertEqual(self.ok(*command), self.ok("--db", str(copy), *command))


class PreparerSummary(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        build_worked_books(self)
        self.folder = self.dir / "for-preparer"
        self.out = self.ok("report", "preparer", "--year", "2025", "--out", str(self.folder))

    def test_the_summary_files_sit_beside_the_csv_files(self) -> None:
        self.assertIn("summary.html", self.out)
        names = sorted(path.name for path in self.folder.iterdir())
        self.assertEqual(names, sorted([
            f"business-{x}-{report}-2025.csv"
            for x in ("a", "b") for report in ("schedule-c", "profit-and-loss", "transactions", "balances")
        ] + ["summary.html", "summary.json"]))
        page = (self.folder / "summary.html").read_bytes()
        self.assertTrue(page.startswith(b"<!doctype html>"))
        summary = json.loads((self.folder / "summary.json").read_bytes().decode("utf-8"))
        self.assertEqual(summary["report"], "preparer-summary")
        self.assertEqual(summary["tax_year"], 2025)
        self.assertEqual([r["report"] for r in summary["reports"]], ["pnl", "schedule-c", "balances"])

    def test_the_summary_figures_tie_to_the_csv_files(self) -> None:
        summary = json.loads((self.folder / "summary.json").read_text(encoding="utf-8"))
        schedule = {b["business"]: b for b in summary["reports"][1]["businesses"]}
        for stem, name in (("a", "Business A"), ("b", "Business B")):
            rollup = read_csv(self.folder / f"business-{stem}-schedule-c-2025.csv")
            lines = {row["line"]: row["amount"] for row in rollup if row["line"]}
            json_lines = {line["line"]: line["amount"]["amount"] for line in schedule[name]["lines"]}
            self.assertEqual(json_lines["31"], lines["31"], name)
        parser = _Text()
        parser.feed((self.folder / "summary.html").read_text(encoding="utf-8"))
        self.assertIn("8,117.53", parser.cells)
        self.assertIn("4,982.25", parser.cells)

    def test_each_csv_is_the_same_bytes_as_its_report(self) -> None:
        for stem, name in (("a", "Business A"), ("b", "Business B")):
            for report, command in (
                ("schedule-c", ("report", "schedule-c", "--year", "2025")),
                ("profit-and-loss", ("report", "pnl", "--period", "2025", "--by", "quarter")),
                ("balances", ("report", "balances", "--period", "2025")),
            ):
                single = self.dir / f"{stem}-{report}.csv"
                self.ok(*command, "--business", name, "--format", "csv", "-o", str(single))
                self.assertEqual(
                    (self.folder / f"business-{stem}-{report}-2025.csv").read_bytes(), single.read_bytes(), (name, report)
                )

    def test_the_rows_file_still_reads(self) -> None:
        detail = parse_csv((self.folder / "business-b-transactions-2025.csv").read_text(encoding="utf-8-sig"))
        self.assertEqual(len(detail), 9)
