"""Profit and loss and Schedule C rollups, including the form changing between years."""
from __future__ import annotations

from datetime import date

from solo_books import books, ledger, reports, schedule_c
from solo_books.errors import BooksError
from solo_books.periods import parse_period
from tests.helpers import BooksCase


class ReportBase(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)

    def book(self, code: str, cents: int, day: date = date(2025, 3, 1), biz=None, acct=None) -> int:
        category = self.category(biz or self.biz, code)
        return ledger.add_manual(self.conn, acct or self.acct, day, cents, f"ROW {code} {cents}", category)


class ProfitAndLoss(ReportBase):
    def test_refund_reduces_its_expense(self) -> None:
        self.book("6140", -10000)
        self.book("6140", 2500)
        pnl = reports.profit_and_loss(self.conn, self.biz, parse_period("2025"))
        self.assertEqual(pnl.expenses[0].total, 7500)
        self.assertEqual(pnl.net, [-7500])

    def test_returns_reduce_income(self) -> None:
        self.book("4000", 10000)
        self.book("4100", -1500)
        pnl = reports.profit_and_loss(self.conn, self.biz, parse_period("2025"))
        self.assertEqual(pnl.total_income, [8500])

    def test_transfer_is_never_income_or_expense(self) -> None:
        self.book("3900", -50000)
        card = self.account(self.biz, "A Card", "credit_card")
        self.book("3900", 50000, acct=card)
        pnl = reports.profit_and_loss(self.conn, self.biz, parse_period("2025"))
        self.assertEqual((pnl.total_income, pnl.total_expenses, pnl.net), ([0], [0], [0]))
        self.assertEqual(pnl.off.excluded[0].total, 0)

    def test_uncategorized_rows_stay_out_and_are_counted(self) -> None:
        self.book("4000", 10000)
        ledger.add_manual(self.conn, self.acct, date(2025, 3, 2), -700, "MYSTERY")
        ledger.add_manual(self.conn, self.acct, date(2025, 3, 3), 300, "MYSTERY IN")
        pnl = reports.profit_and_loss(self.conn, self.biz, parse_period("2025"))
        self.assertEqual(pnl.net, [10000])
        self.assertEqual((pnl.off.uncategorized_count, pnl.off.uncategorized_in, pnl.off.uncategorized_out), (2, 300, 700))
        self.assertIn("WARNING: 2 uncategorized", "\n".join(self.ok("report", "pnl", "--period", "2025").splitlines()))

    def test_buckets_and_edges(self) -> None:
        self.book("4000", 100, date(2025, 1, 1))
        self.book("4000", 200, date(2025, 3, 31))
        self.book("4000", 400, date(2025, 4, 1))
        self.book("4000", 800, date(2026, 1, 1))
        pnl = reports.profit_and_loss(self.conn, self.biz, parse_period("2025"), "quarter")
        self.assertEqual(pnl.income[0].values, [300, 400, 0, 0])
        self.assertEqual(pnl.net, [300, 400, 0, 0])

    def test_businesses_do_not_mix(self) -> None:
        other = self.business("Business B")
        other_acct = self.account(other, "B Checking")
        self.book("4000", 100)
        self.book("4000", 999, biz=other, acct=other_acct)
        self.assertEqual(reports.profit_and_loss(self.conn, self.biz, parse_period("2025")).net, [100])
        self.assertEqual(reports.profit_and_loss(self.conn, other, parse_period("2025")).net, [999])
        out = self.ok("report", "pnl", "--period", "2025")
        self.assertIn("Business A", out)
        self.assertIn("Business B", out)
        self.assertIn("All businesses", out)
        self.assertIn("10.99", out)

    def test_table_output_labels_itself(self) -> None:
        self.book("4000", 123456)
        out = self.ok("report", "pnl", "--period", "2025-Q1", "--business", "Business A", "--by", "month")
        self.assertIn("1,234.56", out)
        self.assertIn("2025-03", out)
        self.assertIn("not tax advice", out)

    def test_many_small_rows_keep_every_cent(self) -> None:
        # 0.07 and 0.10 are the classic values that drift in floating point.
        for offset in range(300):
            self.book("6150", -7, date(2025, 1, 1 + offset % 28))
        for offset in range(300):
            self.book("4000", 10, date(2025, 2, 1 + offset % 28))
        pnl = reports.profit_and_loss(self.conn, self.biz, parse_period("2025"))
        self.assertEqual(pnl.net, [3000 - 2100])


class ScheduleCRollup(ReportBase):
    def test_lines_and_totals(self) -> None:
        self.book("4000", 100000)
        self.book("4900", 500)
        self.book("6000", -1000)
        self.book("6200", -2000)
        self.book("6210", -300)
        report = reports.schedule_c(self.conn, self.biz, 2025)
        self.assertEqual(report.amount("1"), 100000)
        self.assertEqual(report.amount("6"), 500)
        self.assertEqual(report.amount("7"), 100500)
        self.assertEqual(report.amount("8"), 1000)
        self.assertEqual(report.amount("48"), 2300)
        self.assertEqual(report.amount("27b"), 2300)
        self.assertEqual(report.amount("28"), 3300)
        self.assertEqual(report.amount("31"), 97200)
        self.assertTrue(report.ties)

    def test_only_that_calendar_year(self) -> None:
        self.book("4000", 100, date(2024, 12, 31))
        self.book("4000", 200, date(2025, 1, 1))
        self.book("4000", 400, date(2025, 12, 31))
        self.book("4000", 800, date(2026, 1, 1))
        self.assertEqual(reports.schedule_c(self.conn, self.biz, 2025).amount("1"), 600)

    def test_transfer_and_owner_money_never_reach_a_line(self) -> None:
        self.book("3900", -5000)
        self.book("3100", -7000)
        self.book("3000", 9000)
        report = reports.schedule_c(self.conn, self.biz, 2025)
        self.assertEqual(report.amount("31"), 0)
        self.assertTrue(all(line.cents == 0 for line in report.lines))

    def test_interest_moves_between_lines_across_tax_years(self) -> None:
        # On the 2025 form 16b is Other interest. On 2026 it is Vehicle loan, and Other moved to 16c.
        self.book("6065", -1000, date(2025, 5, 1))
        self.book("6063", -200, date(2025, 5, 2))
        self.book("6065", -3000, date(2026, 5, 1))
        self.book("6063", -400, date(2026, 5, 2))
        y2025 = reports.schedule_c(self.conn, self.biz, 2025)
        self.assertEqual(y2025.amount("16b"), 1200)
        y2026 = reports.schedule_c(self.conn, self.biz, 2026)
        self.assertEqual(y2026.amount("16b"), 400)
        self.assertEqual(y2026.amount("16c"), 3000)
        self.assertEqual(y2026.amount("28"), 3400)

    def test_year_without_data_refuses_until_told(self) -> None:
        self.book("4000", 100, date(2027, 1, 1))
        with self.assertRaises(BooksError) as caught:
            reports.schedule_c(self.conn, self.biz, 2027)
        self.assertIn("--lines-year", str(caught.exception))
        report = reports.schedule_c(self.conn, self.biz, 2027, lines_year=2026)
        self.assertEqual(report.amount("1"), 100)
        self.assertTrue(any("2026 form" in w for w in report.warnings))

    def test_category_off_the_form_is_warned_and_tied(self) -> None:
        self.book("4000", 10000)
        books.add_category(self.conn, self.biz["id"], "6999", "Unsorted", "expense")
        self.book("6999", -2500)
        report = reports.schedule_c(self.conn, self.biz, 2025)
        self.assertEqual(report.amount("31"), 10000)
        self.assertEqual(report.pnl_net, 7500)
        self.assertTrue(report.ties)
        self.assertTrue(any("6999" in warning for warning in report.warnings))

    def test_cost_of_goods_without_counts_warns(self) -> None:
        self.book("5000", -1000)
        report = reports.schedule_c(self.conn, self.biz, 2025)
        self.assertEqual(report.amount("4"), 1000)
        self.assertTrue(any("inventory" in w.lower() for w in report.warnings))

    def test_inventory_is_per_year(self) -> None:
        self.book("5000", -1000)
        books.set_inventory(self.conn, self.biz["id"], 2025, 300, 100)
        books.set_inventory(self.conn, self.biz["id"], 2025, 400, 100)
        report = reports.schedule_c(self.conn, self.biz, 2025)
        self.assertEqual(report.amount("42"), 1300)
        self.assertEqual(report.amount("31"), -1300)
        self.assertTrue(report.ties)

    def test_meals_show_what_was_booked_and_say_so(self) -> None:
        self.book("6150", -10000)
        report = reports.schedule_c(self.conn, self.biz, 2025)
        self.assertEqual(report.amount("24b"), 10000)
        out = self.ok("report", "schedule-c", "--year", "2025")
        self.assertIn("full amount booked", out)
        self.assertIn("not tax advice", out)

    def test_text_output_stamps_its_source_and_hides_empty_lines(self) -> None:
        self.book("6000", -1000)
        out = self.ok("report", "schedule-c", "--year", "2025")
        self.assertIn("irs.gov", out)
        self.assertIn("retrieved 2026-11-06", out)
        self.assertIn("Advertising", out)
        self.assertNotIn("Depletion", out)
        self.assertIn("Depletion", self.ok("report", "schedule-c", "--year", "2025", "--all-lines"))

    def test_line_48_lists_its_categories(self) -> None:
        self.book("6200", -1000)
        self.book("6210", -200)
        out = self.ok("report", "schedule-c", "--year", "2025")
        self.assertIn("6200 Software subscriptions", out)
        self.assertIn("6210 Bank fees", out)

    def test_uncategorized_warning_reaches_the_csv(self) -> None:
        ledger.add_manual(self.conn, self.acct, date(2025, 3, 2), -700, "MYSTERY")
        out = self.ok("report", "schedule-c", "--year", "2025", "--format", "csv")
        self.assertIn("1 uncategorized", out)


class FormData(BooksCase):
    def test_each_data_file_is_stamped_and_sound(self) -> None:
        years = schedule_c.available_years()
        self.assertIn(2025, years)
        self.assertIn(2026, years)
        for year in years:
            form = schedule_c.load_form(year)
            self.assertEqual(form.tax_year, year)
            self.assertTrue(form.source_url.startswith("https://www.irs.gov/"))
            self.assertRegex(form.retrieved, r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(form.revision)
            lines = {entry["line"] for entry in form.lines}
            for entry in form.lines:
                self.assertTrue(entry["name"])
                self.assertEqual("compute" in entry, "key" not in entry, entry["line"])
                for term in entry.get("compute", []):
                    self.assertIn(term[0], "+-")
                    self.assertIn(term[1:], lines, entry["line"])

    def test_default_chart_lines_exist_on_every_form(self) -> None:
        biz = self.business()
        for year in schedule_c.available_years():
            form = schedule_c.load_form(year)
            for category in books.list_categories(self.conn, biz["id"]):
                key = category["schedule_c_key"]
                if key:
                    entry = form.find_by_key(key)
                    self.assertIsNotNone(entry, f"{key} on {year}")
                    self.assertEqual(entry["accepts"], category["kind"], key)
                else:
                    self.assertIn(category["kind"], books.OFF_FORM_KINDS)

    def test_published_names_on_the_two_forms(self) -> None:
        y2025, y2026 = schedule_c.load_form(2025), schedule_c.load_form(2026)
        self.assertEqual(y2025.find_by_line("16b")["name"], "Other")
        self.assertEqual(y2026.find_by_line("16b")["name"], "Vehicle loan")
        self.assertEqual(y2026.find_by_line("16c")["name"], "Other")
        self.assertIsNone(y2025.find_by_line("16c"))
        self.assertEqual(schedule_c.line_label(y2025.find_by_line("24b")), "Travel and meals: Deductible meals")

    def test_line_28_adds_every_expense_line(self) -> None:
        for year in schedule_c.available_years():
            form = schedule_c.load_form(year)
            terms = {term[1:] for term in form.find_by_line("28")["compute"]}
            expected = {
                entry["line"]
                for entry in form.lines
                if entry["part"] == "II" and entry["line"] not in ("28", "29", "30", "31")
            }
            self.assertEqual(terms, expected)
