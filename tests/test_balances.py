"""Balances, one row's full story, and the saved inventory counts."""
from __future__ import annotations

import csv
import json

from solo_books.money import plain_cents, parse_cents
from tests.helpers import REPO_ROOT, BooksCase, parse_csv, read_csv

SAMPLES = REPO_ROOT / "samples"


class Balances(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok(
            "account", "add", "A Checking", "--business", "Business A", "--kind", "checking",
            "--opening-balance", "1000.00", "--opening-date", "2025-01-01",
        )
        self.ok(
            "account", "add", "A Card", "--business", "Business A", "--kind", "credit_card",
            "--opening-balance", "50.00", "--opening-date", "2025-01-01",
        )

    def _add(self, account: str, day: str, amount: str, text: str, category: str = "") -> None:
        args = ["txn", "add", account, "--date", day, "--amount", amount, "--description", text]
        if category:
            args += ["--category", category]
        self.ok(*args)

    def _money(self, *extra: str) -> dict:
        rows = parse_csv(self.ok("report", "balances", "--format", "csv", *extra))
        return {row["item"]: row["amount"] for row in rows if row["section"] == "money"}

    def test_start_plus_what_moved_equals_the_end(self) -> None:
        self._add("A Checking", "2025-02-01", "400.00", "CLIENT", "Sales")
        self._add("A Card", "2025-02-02", "-30.00", "CAFE", "Meals")
        self._add("A Checking", "2025-02-03", "-100.00", "OWNER DRAW", "Owner draws")
        self._add("A Checking", "2025-02-04", "-50.00", "CARD PAYMENT", "Transfer")
        self._add("A Card", "2025-02-04", "50.00", "PAYMENT THANK YOU", "Transfer")
        money = self._money("--period", "2025", "--business", "Business A")
        self.assertEqual(money["Money at the start"], "950.00")
        self.assertEqual(money["Net profit or (loss)"], "370.00")
        self.assertEqual(money["Owner draws"], "-100.00")
        self.assertEqual(money["Transfer"], "0.00")
        self.assertEqual(money["Money at the end"], "1220.00")

    def test_a_later_period_starts_where_the_earlier_one_ended(self) -> None:
        self._add("A Checking", "2025-01-15", "200.00", "CLIENT", "Sales")
        self._add("A Checking", "2025-02-15", "300.00", "CLIENT", "Sales")
        january = self._money("--period", "2025-01")
        february = self._money("--period", "2025-02")
        self.assertEqual(january["Money at the end"], february["Money at the start"])
        self.assertEqual(february["Net profit or (loss)"], "300.00")

    def test_a_row_on_the_last_or_first_day_lands_in_one_period_only(self) -> None:
        self._add("A Checking", "2025-01-31", "10.00", "LAST DAY", "Sales")
        self._add("A Checking", "2025-02-01", "20.00", "FIRST DAY", "Sales")
        self.assertEqual(self._money("--period", "2025-01")["Net profit or (loss)"], "10.00")
        self.assertEqual(self._money("--period", "2025-02")["Net profit or (loss)"], "20.00")

    def test_cards_print_what_is_owed(self) -> None:
        self._add("A Card", "2025-03-01", "-25.00", "CAFE", "Meals")
        rows = parse_csv(self.ok("report", "balances", "--period", "2025", "--format", "csv"))
        ends = {row["item"]: row["amount"] for row in rows if row["section"] == "account end"}
        self.assertEqual(ends["A Card (owed)"], "75.00")
        self.assertEqual(ends["A Checking"], "1000.00")
        self.assertIn("Card balances show what is owed", self.ok("report", "balances", "--period", "2025"))

    def test_queue_money_shows_on_its_own_line_and_warns(self) -> None:
        self._add("A Checking", "2025-03-01", "-40.00", "MYSTERY")
        money = self._money("--period", "2025")
        self.assertEqual(money["Waiting for review"], "-40.00")
        self.assertEqual(money["Net profit or (loss)"], "0.00")
        self.assertEqual(money["Money at the end"], "910.00")
        self.assertIn("1 row(s) are waiting for review", self.ok("report", "balances", "--period", "2025"))

    def test_one_sided_transfer_warns(self) -> None:
        self._add("A Checking", "2025-03-01", "-40.00", "MOVE", "Transfer")
        self.assertIn("Transfers net to -40.00", self.ok("report", "balances", "--period", "2025"))

    def test_a_move_between_businesses_cancels_in_the_summary(self) -> None:
        self.ok("business", "add", "Business B")
        self.ok(
            "account", "add", "B Checking", "--business", "Business B", "--kind", "checking",
            "--opening-balance", "0", "--opening-date", "2025-01-01",
        )
        self._add("A Checking", "2025-03-01", "-40.00", "TO B", "Transfer")
        self._add("B Checking", "2025-03-01", "40.00", "FROM A", "Transfer")
        out = self.ok("report", "balances", "--period", "2025")
        self.assertIn("Transfers net to -40.00", out)
        self.assertIn("Transfers net to 40.00", out)
        summary = [line for line in out.splitlines() if line.startswith("All businesses  ")][0].split()
        self.assertEqual(summary[-3], "0.00")

    def test_an_account_opened_in_the_period_gets_its_own_line(self) -> None:
        self.ok(
            "account", "add", "A Savings", "--business", "Business A", "--kind", "savings",
            "--opening-balance", "500.00", "--opening-date", "2025-06-15",
        )
        self._add("A Savings", "2025-06-20", "5.00", "INTEREST", "Other income")
        money = self._money("--period", "2025")
        self.assertEqual(money["Money at the start"], "950.00")
        self.assertEqual(money["Accounts opened in this period"], "500.00")
        self.assertEqual(money["Money at the end"], "1455.00")
        self.assertEqual(self._money("--period", "2025-03")["Money at the end"], "950.00")
        self.assertEqual(self._money("--period", "2025-07-01..2025-12-31")["Money at the start"], "1455.00")

    def test_an_opening_on_the_first_day_of_the_period_is_the_start(self) -> None:
        money = self._money("--period", "2025-01-01..2025-01-31")
        self.assertEqual(money["Money at the start"], "950.00")
        self.assertNotIn("Accounts opened in this period", money)

    def test_no_opening_balance_is_left_out_and_named(self) -> None:
        self.ok("account", "add", "A Other", "--business", "Business A", "--kind", "other")
        self._add("A Other", "2025-03-01", "75.00", "CLIENT", "Sales")
        out = self.ok("report", "balances", "--period", "2025")
        self.assertIn("A Other has no opening balance", out)
        self.assertIn("Profit and loss shows 75.00", out)
        self.assertEqual(self._money("--period", "2025")["Money at the end"], "950.00")

    def test_rows_before_the_opening_date_are_left_out_and_counted(self) -> None:
        self.ok(
            "account", "add", "A Late", "--business", "Business A", "--kind", "checking",
            "--opening-balance", "100.00", "--opening-date", "2025-06-01",
        )
        self._add("A Late", "2025-05-01", "33.00", "EARLY", "Sales")
        out = self.ok("report", "balances", "--period", "2025")
        self.assertIn("1 row(s) dated before an account's opening date", out)
        self.assertEqual(self._money("--period", "2025")["Money at the end"], "1050.00")

    def test_inventory_shows_only_on_december_31_with_a_count(self) -> None:
        self.ok("inventory", "set", "--year", "2025", "--beginning", "10.00", "--ending", "75.00")
        self.assertIn("Money plus inventory: 1,025.00", self.ok("report", "balances", "--period", "2025"))
        self.assertNotIn("Inventory count", self.ok("report", "balances", "--period", "2025-Q1"))
        self.assertNotIn("Inventory count", self.ok("report", "balances", "--period", "2024-01-01..2024-12-31"))

    def test_preparer_folder_holds_the_balances(self) -> None:
        self._add("A Checking", "2025-02-01", "400.00", "CLIENT", "Sales")
        out_dir = self.dir / "out"
        self.ok("report", "preparer", "--year", "2025", "--out", str(out_dir))
        rows = read_csv(out_dir / "business-a-balances-2025.csv")
        money = {row["item"]: row["amount"] for row in rows if row["section"] == "money"}
        self.assertEqual(money["Money at the end"], "1350.00")


class TxnShow(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "A Checking", "--business", "Business A", "--kind", "checking", "--date-col", "Date", "--date-format", "%Y-%m-%d")

    def _import(self, name: str, text: str) -> None:
        self.ok("import", "A Checking", str(self.write(name, text)))

    def test_imported_row_names_every_file_that_held_it(self) -> None:
        self._import("one.csv", "Date,Description,Amount\n2025-01-05,ALPHA COFFEE,-5.00\n")
        self._import("two.csv", "Date,Description,Amount\n2025-01-05,ALPHA COFFEE,-5.00\n2025-01-06,BETA,-1.00\n")
        out = self.ok("txn", "show", "1")
        self.assertIn("one.csv (import 1), two.csv (import 2)", out)
        self.assertIn("waiting for review", out)
        self.ok("imports", "undo", "1")
        out = self.ok("txn", "show", "1")
        self.assertNotIn("one.csv", out)
        self.assertIn("two.csv (import 2)", out)

    def test_says_how_a_row_was_booked(self) -> None:
        self._import("one.csv", "Date,Description,Amount\n2025-01-05,ALPHA COFFEE,-5.00\n")
        self.ok("rules", "add", "--contains", "ALPHA", "--category", "Meals")
        self.assertIn("6150 Meals, by rule 1 (contains 'ALPHA')", self.ok("txn", "show", "1"))
        self.ok("rules", "delete", "1")
        self.assertIn("6150 Meals, by a rule since deleted", self.ok("txn", "show", "1"))
        self.ok("categorize", "Supplies", "1")
        self.assertIn("6120 Supplies, by hand", self.ok("txn", "show", "1"))

    def test_shows_the_note_and_a_hand_added_row(self) -> None:
        self.ok("txn", "add", "A Checking", "--date", "2025-01-05", "--amount", "-5.00", "--description", "TAXI", "--note", "airport")
        out = self.ok("txn", "show", "1")
        self.assertIn("no, added by hand", out)
        self.assertIn("Note:        airport", out)
        self.ok("txn", "note", "1", "client visit")
        self.assertIn("Note:        client visit", self.ok("txn", "show", "1"))

    def test_shows_reconciliation_and_held_back_rules(self) -> None:
        self.ok("account", "opening", "A Checking", "--balance", "100.00", "--date", "2025-01-01")
        self.ok("txn", "add", "A Checking", "--date", "2025-01-05", "--amount", "-5.00", "--description", "TAXI")
        self.conn.execute("INSERT INTO rules (business_id, match_type, pattern, category_id) SELECT business_id, 'contains', 'TAXI', id FROM categories WHERE code = '6140'")
        self.conn.execute("INSERT INTO rule_exceptions (rule_id, transaction_id) VALUES (1, 1)")
        self.assertIn("Held back from rule(s): 1", self.ok("txn", "show", "1"))
        self.assertIn("Reconciled:  no", self.ok("txn", "show", "1"))
        self.ok("reconcile", "run", "A Checking", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "100.00", "--end", "95.00")
        self.ok("reconcile", "finish", "1")
        self.assertIn("finished reconciliation 1", self.ok("txn", "show", "1"))

    def test_unknown_id_fails(self) -> None:
        self.assertIn("no transaction 99", self.fails("txn", "show", "99"))


class InventoryList(BooksCase):
    def test_lists_counts_by_business_and_year(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("business", "add", "Business B")
        self.ok("inventory", "set", "--business", "Business A", "--year", "2025", "--beginning", "100.00", "--ending", "150.00")
        self.ok("inventory", "set", "--business", "Business A", "--year", "2024", "--beginning", "80.00", "--ending", "100.00")
        self.ok("inventory", "set", "--business", "Business B", "--year", "2025", "--beginning", "300.00", "--ending", "250.00")
        lines = self.ok("inventory", "list").splitlines()[2:]
        self.assertEqual([line.split()[2] for line in lines], ["2024", "2025", "2025"])
        self.assertTrue(lines[2].endswith("-50.00"))
        only_b = self.ok("inventory", "list", "--business", "Business B")
        self.assertNotIn("Business A", only_b)


class AgainstIndependentFigures(BooksCase):
    def test_sample_year_ends_on_the_generators_balances(self) -> None:
        expected = json.loads((SAMPLES / "expected-2025.json").read_text(encoding="utf-8"))["ending_balances"]
        self.ok("business", "add", "Business A")
        self.ok("business", "add", "Business B")
        for name, business, kind, opening, mapping in (
            ("A Checking", "Business A", "checking", "12500.00", "a-checking"),
            ("A Card", "Business A", "credit_card", "425.00", "a-card"),
            ("B Checking", "Business B", "checking", "3000.00", "b-checking"),
        ):
            self.ok(
                "account", "add", name, "--business", business, "--kind", kind, "--opening-balance", opening,
                "--opening-date", "2025-01-01", "--mapping", str(SAMPLES / "mappings" / f"{mapping}.json"),
            )
        self.ok("import", "A Checking", str(SAMPLES / "example-bank-a-checking-2025-01-to-07.csv"))
        self.ok("import", "A Checking", str(SAMPLES / "example-bank-a-checking-2025-06-to-12.csv"))
        self.ok("import", "A Card", str(SAMPLES / "example-card-a-2025.csv"))
        self.ok("import", "B Checking", str(SAMPLES / "example-bank-b-checking-2025.csv"))
        self.ok("txn", "add", "A Card", "--date", "2025-03-27", "--amount", "-15.00", "--description", "CITY PARKING GARAGE")
        rows = parse_csv(self.ok("report", "balances", "--period", "2025", "--format", "csv"))
        ends = {row["item"]: row["amount"] for row in rows if row["section"] == "account end"}
        self.assertEqual(ends["A Checking"], expected["A Checking"])
        self.assertEqual(ends["A Card (owed)"], expected["A Card owed"])
        self.assertEqual(ends["B Checking"], expected["B Checking"])

        with open(SAMPLES / "statement-balances-2025.csv", encoding="utf-8", newline="") as handle:
            june = [row for row in csv.DictReader(handle) if row["to"] == "2025-06-30"]
        rows = parse_csv(self.ok("report", "balances", "--period", "2025-06", "--format", "csv"))
        starts = {row["item"].replace(" (owed)", ""): row["amount"] for row in rows if row["section"] == "account start"}
        ends = {row["item"].replace(" (owed)", ""): row["amount"] for row in rows if row["section"] == "account end"}
        for statement in june:
            self.assertEqual(starts[statement["account"]], plain_cents(parse_cents(statement["begin"])))
            self.assertEqual(ends[statement["account"]], plain_cents(parse_cents(statement["end"])))
