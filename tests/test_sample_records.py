"""The history, tags, payees and budgets on upgraded books and over the full sample year."""
from __future__ import annotations

import json
import sqlite3

from solo_books import db, history
from solo_books.money import format_cents, parse_cents
from tests.helpers import REPO_ROOT, BooksCase, parse_csv
from tests.test_safety import make_v1

SAMPLES = REPO_ROOT / "samples"
NEW_TABLES = {"history", "history_rows", "tags", "transaction_tags", "payees", "budgets"}


def _step_that_adds_history() -> int:
    return next(number for number, script in enumerate(db.MIGRATIONS) if "CREATE TABLE history" in script)


class Upgrade(BooksCase):
    def tables(self, path) -> set:
        conn = sqlite3.connect(str(path))
        self.addCleanup(conn.close)
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    def test_the_first_schema_gains_the_new_tables(self) -> None:
        self.conn.close()
        self.db_path.unlink()
        make_v1(self.db_path)
        self.assertFalse(NEW_TABLES & self.tables(self.db_path))
        code, out, err = self.run_cli("history", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("upgrading", err)
        self.assertIn("0 change(s)", out)
        self.assertTrue(NEW_TABLES <= self.tables(self.db_path))
        conn = sqlite3.connect(str(self.db_path))
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.schema_version())
        old_row = conn.execute("SELECT id FROM transactions ORDER BY id LIMIT 1").fetchone()[0]
        self.ok("tag", "add", "Harbor", str(old_row))
        self.ok("txn", "note", str(old_row), "from before the upgrade")
        conn.row_factory = sqlite3.Row
        self.assertEqual([entry["kind"] for entry in history.list_entries(conn)], ["note", "tag-add"])
        self.assertIn("no problems found", self.ok("check"))

    def test_the_step_before_upgrades_too(self) -> None:
        self.conn.close()
        self.db_path.unlink()
        step = _step_that_adds_history()
        raw = sqlite3.connect(str(self.db_path), isolation_level=None)
        for number in range(step):
            db._apply(raw, number)
        self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], step)
        raw.execute("INSERT INTO businesses (name) VALUES ('Business A')")
        raw.close()
        self.assertFalse(NEW_TABLES & self.tables(self.db_path))
        code, _, err = self.run_cli("status")
        self.assertEqual(code, 0, err)
        self.assertTrue(NEW_TABLES <= self.tables(self.db_path))
        self.ok("account", "add", "A Checking", "--kind", "checking")
        self.assertIn("account-add", self.ok("history", "list"))
        self.assertIn("no problems found", self.ok("check"))


class SampleYearRecords(BooksCase):
    """The README walkthrough, then the new commands, against the generator's figures."""

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
        self.ok("import", "A Checking", str(SAMPLES / "example-bank-a-checking-2025-01-to-07.csv"))
        self.ok("import", "A Checking", str(SAMPLES / "example-bank-a-checking-2025-06-to-12.csv"))
        self.ok("import", "A Card", str(SAMPLES / "example-card-a-2025.csv"))
        self.ok("import", "B Checking", str(SAMPLES / "example-bank-b-checking-2025.csv"))
        self.ok("rules", "add", "--business", "Business A", "--contains", "CARD PAYMENT", "--category", "Transfer")
        self.ok("rules", "add", "--business", "Business A", "--contains", "PAYMENT THANK YOU", "--category", "Transfer")
        self.ok("rules", "add", "--business", "Business A", "--contains", "OWNER DRAW", "--category", "Owner draws")
        self.ok("rules", "add", "--business", "Business B", "--contains", "OWNER DRAW", "--category", "Owner draws")
        self.ok("review", stdin="r 6150\nr 6110\n")
        self.ok(
            "txn", "add", "A Card", "--date", "2025-03-27", "--amount", "-15.00",
            "--description", "CITY PARKING GARAGE", "--category", "Travel",
        )
        self.a = self.expected["Business A, after adding the missing parking row"]
        self.b = self.expected["Business B"]

    def ids_booked_to(self, business: str, code: str) -> list:
        return [
            str(row[0])
            for row in self.conn.execute(
                "SELECT t.id FROM transactions t JOIN categories c ON c.id = t.category_id"
                " JOIN businesses b ON b.id = c.business_id WHERE b.name = ? AND c.code = ? ORDER BY t.id",
                (business, code),
            )
        ]

    def test_every_write_of_the_walkthrough_is_in_the_history(self) -> None:
        kinds = {entry["kind"] for entry in history.list_entries(self.conn)}
        self.assertTrue({"business-add", "account-add", "rule-import", "import", "rule-add", "rule-apply",
                         "txn-add"} <= kinds, kinds)
        imported = history.list_entries(self.conn, kind="import")
        rows = sum(entry["row_count"] for entry in imported)
        total = self.conn.execute("SELECT COUNT(*) FROM transactions WHERE import_id IS NOT NULL").fetchone()[0]
        self.assertEqual(rows, total)
        self.assertIn("no problems found", self.ok("check"))

    def test_a_tag_on_every_meal_gives_the_meals_figure(self) -> None:
        meals = self.ids_booked_to("Business A", "6150")
        self.ok("tag", "add", "Meals with clients", *meals)
        rows = parse_csv(self.ok("tag", "list", "--format", "csv"))
        self.assertEqual(rows[0]["rows"], str(len(meals)))
        self.assertEqual(rows[0]["net"], self.a["by_code"]["6150"])
        rows = parse_csv(self.ok("report", "pnl", "--period", "2025", "--tag", "Meals with clients", "--format", "csv"))
        totals = {row["category"]: row["amount"] for row in rows if row["section"] == "total"}
        self.assertEqual(totals["Total expenses"], self.a["by_code"]["6150"].lstrip("-"))
        self.assertEqual(totals["Total income"], "0.00")
        self.assertEqual(len(parse_csv(self.ok("txn", "list", "--tag", "meals with clients", "--format", "csv", "--limit", "1000"))), len(meals))

    def test_the_contractor_figure(self) -> None:
        designers = {
            row[0]
            for row in self.conn.execute(
                "SELECT DISTINCT t.merchant FROM transactions t JOIN categories c ON c.id = t.category_id WHERE c.code = '6030'"
            )
        }
        self.assertEqual(designers, {"PIXEL FOUNDRY CONTRACTOR"})
        self.ok("payee", "set", "PIXEL FOUNDRY CONTRACTOR", "--business", "Business A", "--contractor", "yes")
        rows = parse_csv(self.ok("report", "contractors", "--year", "2025", "--business", "Business A", "--format", "csv"))
        paid = [row for row in rows if row["payee"] == "PIXEL FOUNDRY CONTRACTOR"][0]
        self.assertEqual(paid["net_paid"], self.a["by_code"]["6030"].lstrip("-"))
        self.assertEqual(paid["at_or_over"], "yes")

    def test_payees_add_up_to_the_profit(self) -> None:
        for business, wanted in (("Business A", self.a), ("Business B", self.b)):
            rows = parse_csv(self.ok("payee", "list", "--business", business, "--year", "2025", "--format", "csv"))
            booked = parse_csv(self.ok("report", "payees", "--year", "2025", "--business", business, "--format", "csv", "--limit", "1000"))
            out_rows = [row for row in booked if row["section"] == "money out"]
            in_rows = [row for row in booked if row["section"] == "money in"]
            money_in = sum(parse_cents(row["money_in"]) for row in in_rows)
            money_out = sum(parse_cents(row["money_out"]) for row in out_rows)
            self.assertEqual(format_cents(money_in - money_out).replace(",", ""), wanted["net"], business)
            self.assertTrue(rows, business)

    def test_budget_actuals_are_the_profit_and_loss_figures(self) -> None:
        self.ok("budget", "set", "--business", "Business A", "--category", "Meals", "--year", "2025", "--monthly", "75.00")
        self.ok("budget", "set", "--business", "Business A", "--category", "Sales", "--year", "2025", "--yearly", "96000.00")
        self.ok("budget", "set", "--business", "Business B", "--category", "4000", "--year", "2025", "--yearly", "50000.00")
        for business, wanted in (("Business A", self.a), ("Business B", self.b)):
            rows = parse_csv(self.ok("report", "budget", "--business", business, "--period", "2025", "--format", "csv"))
            for row in rows:
                if row["section"] in ("income", "expense", "cost of goods sold"):
                    signed = parse_cents(wanted["by_code"][row["code"]])
                    shown = parse_cents(row["actual"])
                    self.assertEqual(abs(shown), abs(signed), (business, row["code"]))
            totals = {row["category"]: row for row in rows if row["section"] == "total"}
            self.assertEqual(totals["Total income"]["actual"], wanted["income"], business)
            self.assertEqual(totals["Total expenses"]["actual"], wanted["expenses"], business)
            self.assertEqual(totals["Net profit or (loss)"]["actual"], wanted["net"], business)
        rows = parse_csv(self.ok("report", "budget", "--business", "Business A", "--period", "2025", "--format", "csv"))
        meals = [row for row in rows if row["code"] == "6150"][0]
        self.assertEqual((meals["budget"], meals["actual"], meals["difference"], meals["percent_used"]), ("900.00", "823.48", "-76.52", "91.5"))
        self.ok("budget", "copy", "--from", "2025", "--to", "2026")
        rows = parse_csv(self.ok("report", "budget", "--business", "Business A", "--period", "2026-Q1", "--format", "csv"))
        sales = [row for row in rows if row["code"] == "4000"][0]
        self.assertEqual((sales["budget"], sales["actual"]), ("24000.00", "0.00"))
