"""The README walkthrough over a full synthetic year, against totals from the script that invented it.

samples/expected-2025.json was written by the generator that made up the rows and knows each row's true
category. It never ran this program, so a match here is a second proof that the totals are right.
"""
from __future__ import annotations

import csv
import json

from solo_books.money import format_cents, parse_cents
from tests.helpers import REPO_ROOT, BooksCase, parse_csv

SAMPLES = REPO_ROOT / "samples"


class SampleYear(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.expected = json.loads((SAMPLES / "expected-2025.json").read_text(encoding="utf-8"))
        self.ok("business", "add", "Business A", "--activity", "Consulting")
        self.ok("business", "add", "Business B", "--activity", "Online shop")
        for name, business, kind, opening, mapping in (
            ("A Checking", "Business A", "checking", "12500.00", "a-checking"),
            ("A Card", "Business A", "credit_card", "425.00", "a-card"),
            ("B Checking", "Business B", "checking", "3000.00", "b-checking"),
        ):
            self.ok(
                "account", "add", name, "--business", business, "--kind", kind, "--opening-balance", opening,
                "--opening-date", "2025-01-01", "--mapping", str(SAMPLES / "mappings" / f"{mapping}.json"),
            )
        self.ok("rules", "import", str(SAMPLES / "rules-business-a.csv"), "--business", "Business A")
        self.ok("rules", "import", str(SAMPLES / "rules-business-b.csv"), "--business", "Business B")
        self.first_import = self.ok("import", "A Checking", str(SAMPLES / "example-bank-a-checking-2025-01-to-07.csv"))
        self.second_import = self.ok("import", "A Checking", str(SAMPLES / "example-bank-a-checking-2025-06-to-12.csv"))
        self.ok("import", "A Card", str(SAMPLES / "example-card-a-2025.csv"))
        self.ok("import", "B Checking", str(SAMPLES / "example-bank-b-checking-2025.csv"))
        self.ok("rules", "add", "--business", "Business A", "--contains", "CARD PAYMENT", "--category", "Transfer")
        self.ok("rules", "add", "--business", "Business A", "--contains", "PAYMENT THANK YOU", "--category", "Transfer")
        self.ok("rules", "add", "--business", "Business A", "--contains", "OWNER DRAW", "--category", "Owner draws")
        self.ok("rules", "add", "--business", "Business B", "--contains", "OWNER DRAW", "--category", "Owner draws")

    def _review(self) -> None:
        self.ok("review", stdin="r 6150\nr 6110\n")
        self.assertIn("Nothing waiting", self.ok("review"))

    def _add_missing_row(self) -> None:
        self.ok(
            "txn", "add", "A Card", "--date", "2025-03-27", "--amount", "-15.00",
            "--description", "CITY PARKING GARAGE", "--category", "Travel",
        )

    def _count(self, name: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE a.name = ?", (name,)
        ).fetchone()[0]

    def test_overlap_and_pending_leave_the_right_row_counts(self) -> None:
        counts = self.expected["row_counts"]
        self.assertEqual(self._count("A Checking"), counts["A Checking"])
        self.assertEqual(self._count("A Card"), counts["A Card export"])
        self.assertEqual(self._count("B Checking"), counts["B Checking"])
        self.assertNotIn("already in books:   0", self.second_import)

    def test_review_clears_with_rules(self) -> None:
        self._review()
        hits = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE category_source = 'rule'").fetchone()[0]
        total = self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self.assertEqual(hits, total)

    def test_totals_match_the_generator(self) -> None:
        self._review()
        self._add_missing_row()
        self._check_totals()

    def test_the_readme_rule_steps_leave_the_totals_alone(self) -> None:
        self.ok(
            "rules", "add", "--business", "Business A", "--exact", "REDWOOD PLAZA RENT", "--amount-min", "850.00",
            "--amount-max", "850.00", "--day-from", "1", "--day-to", "10", "--note", "Suite 4 lease", "--category", "Office rent",
        )
        rent_rule = self.conn.execute("SELECT MAX(id) FROM rules").fetchone()[0]
        self.assertIn("now number 1", self.ok("rules", "move", str(rent_rule), "--to", "1"))
        self.ok("rules", "apply", "--recheck")
        self.ok("rules", "explain", "2")
        pack = self.dir / "rules-business-a.json"
        self.ok("rules", "export", "--business", "Business A", "--format", "json", "-o", str(pack))
        self.assertIn("Added 0 rule(s)", self.ok("rules", "import", str(pack), "--business", "Business A"))
        self._review()
        self._add_missing_row()
        self._check_totals()

    def _check_totals(self) -> None:
        for business, key in (("Business A", "Business A, after adding the missing parking row"), ("Business B", "Business B")):
            wanted = self.expected[key]
            rows = parse_csv(self.ok("report", "pnl", "--period", "2025", "--business", business, "--format", "csv"))
            totals = {row["category"]: row["amount"] for row in rows if row["section"] == "total"}
            self.assertEqual(totals["Total income"], wanted["income"], business)
            self.assertEqual(totals["Total expenses"], wanted["expenses"], business)
            self.assertEqual(totals["Net profit or (loss)"], wanted["net"], business)
            codes = {row["code"]: row["amount"] for row in rows if row["code"]}
            for code, signed in wanted["by_code"].items():
                if signed == "0.00" and code not in codes:
                    continue
                if code in ("3100", "3900", "4000", "4900"):
                    self.assertEqual(codes[code], signed, code)
                elif signed.startswith("-"):
                    self.assertEqual(codes[code], signed[1:] if code != "4100" else signed, code)
                else:
                    self.assertEqual(codes[code], "-" + signed, code)

    def test_every_statement_of_the_year_ties_and_locks(self) -> None:
        self._add_missing_row()
        with open(SAMPLES / "statement-balances-2025.csv", encoding="utf-8", newline="") as handle:
            statements = list(csv.DictReader(handle))
        self.assertEqual(len(statements), 36)
        for number, statement in enumerate(statements, start=1):
            out = self.ok(
                "reconcile", "run", statement["account"], "--from", statement["from"], "--to", statement["to"],
                "--begin", statement["begin"], "--end", statement["end"],
            )
            self.assertIn("ties to the cent", out, statement)
            self.ok("reconcile", "finish", str(number))
        balances = self.expected["ending_balances"]
        for account, owed in (("A Checking", "A Checking"), ("A Card", "A Card owed"), ("B Checking", "B Checking")):
            self.assertIn(format_cents(parse_cents(balances[owed])), self.ok("account", "show", account))

    def test_march_card_statement_finds_the_missing_row(self) -> None:
        with open(SAMPLES / "statement-balances-2025.csv", encoding="utf-8", newline="") as handle:
            march = [s for s in csv.DictReader(handle) if s["account"] == "A Card" and s["from"] == "2025-03-01"][0]
        args = (
            "reconcile", "run", "A Card", "--from", march["from"], "--to", march["to"], "--begin", march["begin"],
            "--end", march["end"], "--statement", str(SAMPLES / "example-card-a-2025-03-statement.csv"),
        )
        code, out, _ = self.run_cli(*args)
        self.assertEqual(code, 1)
        self.assertIn("Difference: 15.00", out)
        self.assertIn("CITY PARKING GARAGE", out)
        self._add_missing_row()
        self.assertIn("ties to the cent", self.ok(*args))

    def test_schedule_c_ties_to_profit_for_both_businesses(self) -> None:
        self._review()
        self._add_missing_row()
        for business, key in (("Business A", "Business A, after adding the missing parking row"), ("Business B", "Business B")):
            rows = parse_csv(self.ok("report", "schedule-c", "--year", "2025", "--business", business, "--format", "csv"))
            lines = {row["line"]: row["amount"] for row in rows if row["line"]}
            self.assertEqual(lines["31"], self.expected[key]["net"], business)
