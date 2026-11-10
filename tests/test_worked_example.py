"""The program against totals added up by hand. See tests/fixtures/worked/WORKED.md."""
from __future__ import annotations

from tests.helpers import WORKED, BooksCase, build_worked_books, parse_csv, read_csv, worked_expected


class WorkedExample(BooksCase):
    """Runs the whole job through the command line, the way a person would."""

    def setUp(self) -> None:
        super().setUp()
        self.expected = worked_expected()
        build_worked_books(self, review=False)
        self._review_what_the_rules_missed()

    def _review_what_the_rules_missed(self) -> None:
        # The rules leave the card payments, the draw and the cafe for a person to decide.
        queue = self.conn.execute(
            "SELECT id, description FROM transactions WHERE category_id IS NULL ORDER BY id"
        ).fetchall()
        descriptions = sorted({row["description"] for row in queue})
        self.assertEqual(
            descriptions,
            [
                "EXAMPLE CARD PAYMENT",
                "OWNER DRAW TO PERSONAL",
                "PAYMENT THANK YOU",
                "SQ *BLUE FERN CAFE 0105 OGDEN UT",
                "SQ *BLUE FERN CAFE 0109 OGDEN UT",
            ],
        )
        decide = {"EXAMPLE CARD PAYMENT": "Transfer", "PAYMENT THANK YOU": "Transfer", "OWNER DRAW TO PERSONAL": "3100"}
        for row in queue:
            self.ok("categorize", decide.get(row["description"], "6150"), str(row["id"]))
        self.assertIn("0 row(s) waiting", self.ok("review", "--list"))

    def _pnl(self, business: str, *extra: str) -> list:
        return parse_csv(
            self.ok("report", "pnl", "--period", "2025-Q1", "--business", business, "--format", "csv", *extra)
        )

    def _schedule(self, business: str) -> dict:
        rows = parse_csv(self.ok("report", "schedule-c", "--year", "2025", "--business", business, "--format", "csv"))
        return {row["line"]: row["amount"] for row in rows if row["line"]}

    def _by_code(self, rows: list) -> dict:
        return {row["code"]: row["amount"] for row in rows if row["code"]}

    def _by_total(self, rows: list) -> dict:
        return {row["category"]: row["amount"] for row in rows if row["section"] == "total"}

    def test_business_a_profit_and_loss(self) -> None:
        rows = self._pnl("Business A")
        expected = self.expected["business_a"]["pnl"]
        codes = self._by_code(rows)
        for code in ("4000", "4900", "6000", "6080", "6105", "6130", "6140", "6150", "6200", "6210"):
            self.assertEqual(codes[code], expected[code], code)
        totals = self._by_total(rows)
        self.assertEqual(totals["Total income"], expected["total_income"])
        self.assertEqual(totals["Total expenses"], expected["total_expenses"])
        self.assertEqual(totals["Net profit or (loss)"], expected["net"])

    def test_transfers_and_owner_money_stay_out_of_profit(self) -> None:
        codes = self._by_code(self._pnl("Business A"))
        excluded = self.expected["business_a"]["excluded"]
        self.assertEqual(codes["3100"], excluded["3100"])
        self.assertEqual(codes["3900"], excluded["3900"])

    def test_business_a_by_month(self) -> None:
        rows = self._pnl("Business A", "--by", "month")
        for month, wanted in self.expected["business_a"]["pnl_by_month"].items():
            totals = {r["category"]: r["amount"] for r in rows if r["section"] == "total" and r["period"] == month}
            self.assertEqual(totals["Total income"], wanted["income"], month)
            self.assertEqual(totals["Total expenses"], wanted["expenses"], month)
            self.assertEqual(totals["Net profit or (loss)"], wanted["net"], month)

    def test_business_b_profit_and_loss_with_costs(self) -> None:
        rows = self._pnl("Business B")
        expected = self.expected["business_b"]["pnl"]
        codes = self._by_code(rows)
        self.assertEqual(codes["4000"], expected["4000"])
        # Returns print as a reduction of income.
        self.assertEqual(codes["4100"], "-" + expected["4100"])
        for code in ("5000", "5200", "5300", "6200"):
            self.assertEqual(codes[code], expected[code], code)
        totals = self._by_total(rows)
        self.assertEqual(totals["Total income"], expected["total_income"])
        self.assertEqual(totals["Gross profit"], expected["gross_profit"])
        self.assertEqual(totals["Net profit or (loss)"], expected["net"])

    def test_schedule_c_business_a(self) -> None:
        lines = self._schedule("Business A")
        for line, amount in self.expected["business_a"]["schedule_c_2025"].items():
            self.assertEqual(lines[line], amount, f"line {line}")

    def test_schedule_c_business_b_with_inventory(self) -> None:
        lines = self._schedule("Business B")
        for line, amount in self.expected["business_b"]["schedule_c_2025"].items():
            self.assertEqual(lines[line], amount, f"line {line}")

    def test_balances(self) -> None:
        out = self.ok("account", "show", "A Checking")
        self.assertIn("11,928.15", out)
        self.assertIn("510.62", self.ok("account", "show", "A Card"))
        self.assertIn("5,732.25", self.ok("account", "show", "B Checking"))

    def test_balances_report_matches_the_hand_cash_check(self) -> None:
        def money(period: str) -> dict:
            rows = parse_csv(
                self.ok("report", "balances", "--period", period, "--business", "Business A", "--format", "csv")
            )
            return {row["item"]: row["amount"] for row in rows if row["section"] == "money"}

        quarter = money("2025-Q1")
        self.assertEqual(quarter["Money at the start"], "4800.00")
        self.assertEqual(quarter["Net profit or (loss)"], "8117.53")
        self.assertEqual(quarter["Owner draws"], "-1500.00")
        self.assertEqual(quarter["Transfer"], "0.00")
        self.assertEqual(quarter["Money at the end"], "11417.53")
        february = money("2025-02")
        self.assertEqual(february["Money at the start"], "7728.30")
        self.assertEqual(february["Money at the end"], "9924.16")

    def test_every_month_reconciles_and_locks(self) -> None:
        recon = self.expected["reconcile"]
        for account, periods in (("A Checking", recon["a_checking"]), ("A Card", recon["a_card"])):
            for period in periods:
                out = self.ok(
                    "reconcile", "run", account, "--from", period["from"], "--to", period["to"],
                    "--begin", period["begin"], "--end", period["end"],
                )
                self.assertIn("ties to the cent", out)
                recon_id = self.conn.execute(
                    "SELECT MAX(id) FROM reconciliations"
                ).fetchone()[0]
                self.ok("reconcile", "finish", str(recon_id))
        unlocked = self.conn.execute(
            "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id"
            " WHERE a.name IN ('A Checking', 'A Card') AND t.reconciliation_id IS NULL"
        ).fetchone()[0]
        self.assertEqual(unlocked, 0)

    def test_card_statement_shows_the_missing_row_then_ties_once_added(self) -> None:
        period = self.expected["reconcile"]["a_card_march_with_parking"]
        code, out, _ = self.run_cli(
            "reconcile", "run", "A Card", "--from", period["from"], "--to", period["to"],
            "--begin", period["begin"], "--end", period["end"],
            "--statement", str(WORKED / "a_card_march_statement.csv"),
        )
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT MATCH", out)
        self.assertIn("Difference: 15.00", out)
        self.assertIn("more money out", out)
        self.assertIn("On the statement, not in the books", out)
        self.assertIn("CITY PARKING GARAGE", out)

        self.ok(
            "txn", "add", "A Card", "--date", "2025-03-27", "--amount", "-15.00",
            "--description", "CITY PARKING GARAGE", "--category", "Travel",
        )
        out = self.ok(
            "reconcile", "run", "A Card", "--from", period["from"], "--to", period["to"],
            "--begin", period["begin"], "--end", period["end"],
            "--statement", str(WORKED / "a_card_march_statement.csv"),
        )
        self.assertIn("Every line matches", out)
        self.assertIn("ties to the cent", out)
        # The added row reaches the reports too: travel goes from 530.00 to 545.00.
        self.assertEqual(self._by_code(self._pnl("Business A"))["6140"], "545.00")

    def test_preparer_export_carries_the_same_figures(self) -> None:
        out_dir = self.dir / "for-preparer"
        output = self.ok("report", "preparer", "--year", "2025", "--out", str(out_dir))
        self.assertIn("not tax or accounting advice", output)
        rollup = read_csv(out_dir / "business-a-schedule-c-2025.csv")
        lines = {row["line"]: row["amount"] for row in rollup if row["line"]}
        self.assertEqual(lines["31"], self.expected["business_a"]["schedule_c_2025"]["31"])
        self.assertTrue(any("not tax or accounting advice" in row["description"] for row in rollup))
        self.assertTrue(any("irs.gov" in row["description"] for row in rollup))
        detail = read_csv(out_dir / "business-b-transactions-2025.csv")
        self.assertEqual(len(detail), 9)
        self.assertEqual(
            sum(int(row["amount"].replace(".", "")) for row in detail),
            5732_25 - 1000_00,
        )
        pnl = read_csv(out_dir / "business-a-profit-and-loss-2025.csv")
        self.assertTrue(any(row["period"] == "2025-Q1" and row["category"] == "Net profit or (loss)" for row in pnl))
