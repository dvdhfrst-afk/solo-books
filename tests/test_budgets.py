"""Budgets: a plan per category per year, set by the month or the year, and a report against the books."""
from __future__ import annotations

from datetime import date

from solo_books import budgets, history, ledger
from solo_books.errors import BooksError
from solo_books.periods import parse_period
from tests.helpers import BooksCase, parse_csv


class Arithmetic(BooksCase):
    def test_a_year_spreads_over_the_months_to_the_cent(self) -> None:
        for yearly in (0, 1, 11, 12, 100001, 9600000, 123456789):
            shares = [budgets.month_share(yearly, month) for month in range(1, 13)]
            self.assertEqual(sum(shares), yearly)
            self.assertLessEqual(max(shares) - min(shares), 1)

    def test_percent_used_in_whole_numbers(self) -> None:
        self.assertEqual(budgets.percent_used(82348, 90000), "91.5%")
        self.assertEqual(budgets.percent_used(1049, 1000), "104.9%")
        self.assertEqual(budgets.percent_used(0, 1000), "0.0%")
        self.assertEqual(budgets.percent_used(-500, 1000), "-50.0%")
        self.assertEqual(budgets.percent_used(2, 3), "66.7%")
        self.assertIsNone(budgets.percent_used(500, 0))


class Setting(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)
        self.meals = self.category(self.biz, "6150")

    def months(self, year: int = 2025) -> dict:
        return budgets._monthly(budgets._amounts(self.conn, self.meals["id"], year))

    def test_monthly_and_yearly(self) -> None:
        out = self.ok("budget", "set", "--business", "Business A", "--category", "Meals", "--year", "2025", "--monthly", "75.00")
        self.assertIn("900.00 for the year", out)
        self.assertEqual(set(self.months().values()), {7500})
        self.ok("budget", "set", "--business", "Business A", "--category", "6150", "--year", "2025", "--yearly", "1000.00")
        self.assertEqual(sum(self.months().values()), 100000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM budgets").fetchone()[0], 1)

    def test_one_month_on_top_of_a_year_spreads_the_year_first(self) -> None:
        self.ok("budget", "set", "--business", "Business A", "--category", "Meals", "--year", "2025", "--yearly", "1200.00")
        out = self.ok("budget", "set", "--business", "Business A", "--category", "Meals", "--year", "2025", "--monthly", "300.00", "--month", "12")
        self.assertIn("spread over the twelve months first", out)
        self.assertIn("1,400.00 for the year", out)
        self.assertEqual(self.months()[12], 30000)
        self.assertEqual(self.months()[1], 10000)

    def test_mistakes_are_refused(self) -> None:
        base = ("budget", "set", "--business", "Business A", "--year", "2025")
        self.assertIn("transfer", self.fails(*base, "--category", "Transfer", "--monthly", "5.00"))
        self.assertIn("positive amount", self.fails(*base, "--category", "Meals", "--monthly", "-5.00"))
        self.assertIn("--month with --monthly", self.fails(*base, "--category", "Meals", "--yearly", "5.00", "--month", "3"))
        self.assertEqual(self.run_cli(*base, "--category", "Meals", "--monthly", "5.00", "--yearly", "60.00")[0], 2)
        self.assertEqual(self.run_cli(*base, "--category", "Meals", "--monthly", "5.00", "--month", "13")[0], 2)
        self.assertEqual(self.run_cli(*base, "--category", "Meals", "--monthly", "5.001")[0], 2)
        other = self.business("Business B")
        with self.assertRaises(BooksError):
            budgets.set_budget(self.conn, other, self.meals, 2025, monthly=500)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM budgets").fetchone()[0], 0)
        self.assertEqual(history.list_entries(self.conn, kind="budget"), [])

    def test_list_delete_and_copy(self) -> None:
        self.ok("budget", "set", "--business", "Business A", "--category", "Meals", "--year", "2025", "--monthly", "75.00")
        self.ok("budget", "set", "--business", "Business A", "--category", "Sales", "--year", "2025", "--yearly", "96000.00")
        out = self.ok("budget", "list")
        self.assertIn("75.00 a month", out)
        self.assertIn("96,000.00", out)
        rows = parse_csv(self.ok("budget", "list", "--format", "csv", "--year", "2025"))
        sales = [row for row in rows if row["code"] == "4000"][0]
        self.assertEqual((sales["month_01"], sales["month_12"]), ("8000.00", "8000.00"))
        self.assertIn("copied 2 category budget(s)", self.ok("budget", "copy", "--from", "2025", "--to", "2026"))
        self.assertIn("already has", self.fails("budget", "copy", "--from", "2025", "--to", "2026", "--business", "Business A"))
        self.ok("budget", "copy", "--from", "2025", "--to", "2026", "--business", "Business A", "--replace")
        self.assertEqual(self.months(2026), self.months(2025))
        self.assertIn("no business has a budget for 2030", self.fails("budget", "copy", "--from", "2030", "--to", "2031"))
        out = self.ok("budget", "delete", "--business", "Business A", "--category", "Meals", "--year", "2026")
        self.assertIn("900.00 for the year", out)
        self.assertIn("has no budget for 2026", self.fails("budget", "delete", "--business", "Business A", "--category", "Meals", "--year", "2026"))
        kinds = [entry["kind"] for entry in history.list_entries(self.conn, kind="budget")][::-1]
        self.assertEqual(kinds, ["budget-set", "budget-set", "budget-copy", "budget-copy", "budget-delete"])
        deleted = history.entry_detail(history.list_entries(self.conn, kind="budget-delete")[0])
        self.assertEqual(deleted["before"], {str(month): 7500 for month in range(1, 13)})

    def test_retiring_or_deleting_keeps_budgets_honest(self) -> None:
        self.ok("budget", "set", "--business", "Business A", "--category", "Meals", "--year", "2025", "--monthly", "75.00")
        self.assertEqual(budgets.problems(self.conn), [])
        self.conn.execute("INSERT INTO budgets (business_id, category_id, year, month, amount_cents) VALUES (?, ?, 2025, 0, 100)",
                          (self.biz["id"], self.meals["id"]))
        self.assertTrue(budgets.problems(self.conn))
        self.assertEqual(self.run_cli("check")[0], 1)


class Report(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)
        meals, sales = self.category(self.biz, "6150"), self.category(self.biz, "4000")
        ledger.add_manual(self.conn, self.acct, date(2025, 1, 10), -8000, "BLUE FERN CAFE", meals)
        ledger.add_manual(self.conn, self.acct, date(2025, 2, 10), -2000, "BLUE FERN CAFE", meals)
        ledger.add_manual(self.conn, self.acct, date(2025, 4, 10), 900000, "HARBOR LANE STUDIO", sales)
        ledger.add_manual(self.conn, self.acct, date(2025, 2, 11), -3000, "HARDWARE BARN", self.category(self.biz, "6110"))
        ledger.add_manual(self.conn, self.acct, date(2025, 2, 12), -999, "SOMETHING NEW")
        self.ok("budget", "set", "--business", "Business A", "--category", "Meals", "--year", "2025", "--monthly", "50.00")
        self.ok("budget", "set", "--business", "Business A", "--category", "Sales", "--year", "2025", "--yearly", "96000.00")

    def test_the_year(self) -> None:
        report = budgets.budget_report(self.conn, self.biz, parse_period("2025"))
        meals = [line for line in report.expenses if line.code == "6150"][0]
        self.assertEqual((meals.budget, meals.actual, meals.difference), (60000, 10000, -50000))
        repairs = [line for line in report.expenses if line.code == "6110"][0]
        self.assertEqual((repairs.budget, repairs.actual), (0, 3000))
        self.assertEqual(report.total_income, (9600000, 900000))
        self.assertEqual(report.net, (9600000 - 60000, 900000 - 13000))
        self.assertEqual(report.off.uncategorized_count, 1)
        out = self.ok("report", "budget", "--business", "Business A", "--period", "2025")
        self.assertIn("16.7%", out)
        self.assertIn("NOTE: 1 category(s) have money but no budget", out)
        self.assertIn("WARNING: 1 uncategorized row(s)", out)

    def test_a_quarter_and_a_month(self) -> None:
        rows = parse_csv(self.ok("report", "budget", "--business", "Business A", "--period", "2025-Q1", "--format", "csv"))
        meals = [row for row in rows if row["code"] == "6150"][0]
        self.assertEqual((meals["budget"], meals["actual"], meals["difference"], meals["percent_used"]), ("150.00", "100.00", "-50.00", "66.7"))
        sales = [row for row in rows if row["code"] == "4000"][0]
        self.assertEqual((sales["budget"], sales["actual"]), ("24000.00", "0.00"))
        rows = parse_csv(self.ok("report", "budget", "--business", "Business A", "--period", "2025-01", "--format", "csv"))
        meals = [row for row in rows if row["code"] == "6150"][0]
        self.assertEqual((meals["budget"], meals["actual"], meals["percent_used"]), ("50.00", "80.00", "160.0"))
        net = [row for row in rows if row["category"] == "Net profit or (loss)"][0]
        self.assertEqual((net["budget"], net["actual"]), ("7950.00", "-80.00"))

    def test_only_whole_months(self) -> None:
        err = self.fails("report", "budget", "--business", "Business A", "--period", "2025-01-15..2025-02-20")
        self.assertIn("first of a month", err)
        self.ok("report", "budget", "--business", "Business A", "--period", "2025-01-01..2025-02-28")

    def test_to_a_file(self) -> None:
        target = self.dir / "budget.csv"
        self.ok("report", "budget", "--business", "Business A", "--period", "2025", "--format", "csv", "-o", str(target))
        self.assertTrue(target.read_text(encoding="utf-8-sig").startswith("business,period,period_start"))

    def test_help(self) -> None:
        for command in ("set", "list", "delete", "copy"):
            self.assertIn("usage:", self.ok("budget", command, "--help"))
        self.assertIn("usage:", self.ok("report", "budget", "--help"))
