"""The program against totals added up by hand. See tests/fixtures/worked/WORKED.md."""
from __future__ import annotations

from tests.helpers import WORKED, BooksCase, parse_csv, read_csv, worked_expected


class WorkedExample(BooksCase):
    """Runs the whole job through the command line, the way a person would."""

    def setUp(self) -> None:
        super().setUp()
        self.expected = worked_expected()
        self.ok("business", "add", "Business A", "--activity", "Consulting")
        self.ok("business", "add", "Business B", "--activity", "Online shop")
        self.ok(
            "account", "add", "A Checking", "--business", "Business A", "--kind", "checking",
            "--institution", "Example Bank", "--opening-balance", "5000.00", "--opening-date", "2025-01-01",
            "--date-col", "Date", "--description-col", "Description", "--amount-col", "Amount",
        )
        self.ok(
            "account", "add", "A Card", "--business", "Business A", "--kind", "credit_card",
            "--institution", "Example Card", "--opening-balance", "200.00", "--opening-date", "2025-01-01",
            "--date-col", "Post Date", "--description-col", "Description", "--amount-col", "Amount",
        )
        self.ok(
            "account", "add", "B Checking", "--business", "Business B", "--kind", "checking",
            "--opening-balance", "1000.00", "--opening-date", "2025-01-01", "--date-col", "Date",
            "--description-col", "Description", "--debit-col", "Debit", "--credit-col", "Credit",
            "--status-col", "Status", "--skip-lines", "2",
        )
        self.ok("rules", "import", str(WORKED / "rules_a.csv"), "--business", "Business A")
        self.ok("rules", "import", str(WORKED / "rules_b.csv"), "--business", "Business B")
        self.ok("import", "A Checking", str(WORKED / "a_checking_jan_feb.csv"))
        self.ok("import", "A Checking", str(WORKED / "a_checking_feb_mar.csv"))
        self.ok("import", "A Card", str(WORKED / "a_card_q1.csv"))
        self.ok("import", "B Checking", str(WORKED / "b_checking_q1.csv"))
        self._review_what_the_rules_missed()
        self.ok("inventory", "set", "--business", "Business B", "--year", "2025", "--beginning", "500.00", "--ending", "750.00")

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
        self.assertIn("not tax advice", output)
        rollup = read_csv(out_dir / "business-a-schedule-c-2025.csv")
        lines = {row["line"]: row["amount"] for row in rollup if row["line"]}
        self.assertEqual(lines["31"], self.expected["business_a"]["schedule_c_2025"]["31"])
        self.assertTrue(any("not tax advice" in row["description"] for row in rollup))
        self.assertTrue(any("irs.gov" in row["description"] for row in rollup))
        detail = read_csv(out_dir / "business-b-transactions-2025.csv")
        self.assertEqual(len(detail), 9)
        self.assertEqual(
            sum(int(row["amount"].replace(".", "")) for row in detail),
            5732_25 - 1000_00,
        )
        pnl = read_csv(out_dir / "business-a-profit-and-loss-2025.csv")
        self.assertTrue(any(row["period"] == "2025-Q1" and row["category"] == "Net profit or (loss)" for row in pnl))
