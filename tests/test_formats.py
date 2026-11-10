"""Report formats: the registry, the shape of each format, and what each does with hostile text.

Figures are checked against tests/fixtures/worked/expected.json, which was added up by hand.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
from datetime import date
from unittest import mock

from solo_books import analysis, books, formats, ledger
from solo_books.cli import build_parser
from solo_books.formats.base import Format, Report
from solo_books.periods import parse_period
from tests.helpers import BooksCase, build_worked_books, parse_csv, worked_expected

REPORT_COMMANDS = {
    "pnl": ("report", "pnl", "--period", "2025-Q1"),
    "balances": ("report", "balances", "--period", "2025-Q1"),
    "schedule-c": ("report", "schedule-c", "--year", "2025"),
    "cashflow": ("report", "cashflow", "--period", "2025-Q1"),
    "trend": ("report", "trend", "--years", "2024..2025"),
    "categories": ("report", "categories", "--period", "2025-Q1"),
}


def cents(entry: dict) -> int:
    """Read an amount from the JSON, checking its two halves agree."""
    whole, fraction = entry["amount"].lstrip("-").split(".")
    value = int(whole) * 100 + int(fraction)
    if entry["amount"].startswith("-"):
        value = -value
    assert value == entry["cents"], entry
    return entry["cents"]


def to_cents(text: str) -> int:
    negative = text.startswith("-")
    whole, fraction = text.lstrip("-").split(".")
    value = int(whole) * 100 + int(fraction)
    return -value if negative else value


class Registry(BooksCase):
    def test_every_report_offers_every_registered_format(self) -> None:
        self.assertEqual(formats.names(), ("table", "csv", "json", "html", "markdown"))
        parser = build_parser()
        for command in REPORT_COMMANDS.values():
            for name in formats.names():
                args = parser.parse_args([*command, "--format", name])
                self.assertEqual(args.format, name)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args([*REPORT_COMMANDS["pnl"], "--format", "xml"])

    def test_a_new_format_is_one_registration(self) -> None:
        class Counting(Format):
            name = "count"
            help = "how many businesses"

            def render(self, report: Report) -> str:
                return f"{report.kind}: {len(report.parts)}"

        self.business()
        with mock.patch.dict(formats.FORMATS, {"count": Counting()}):
            for kind, command in REPORT_COMMANDS.items():
                self.assertEqual(self.ok(*command, "--format", "count").strip(), f"{kind}: 1")

    def test_unknown_format_name_is_a_plain_error(self) -> None:
        with self.assertRaises(Exception) as caught:
            formats.get("xml")
        self.assertIn("no report format 'xml'", str(caught.exception))

    def test_table_is_the_default(self) -> None:
        self.business()
        for command in REPORT_COMMANDS.values():
            self.assertEqual(self.ok(*command), self.ok(*command, "--format", "table"))


class WorkedFormats(BooksCase):
    """Each format on the hand-checked example."""

    def setUp(self) -> None:
        super().setUp()
        build_worked_books(self)
        self.expected = worked_expected()

    def json_report(self, kind: str, *extra: str) -> dict:
        data = json.loads(self.ok(*REPORT_COMMANDS[kind], *extra, "--format", "json"))
        self.assertEqual(data["format_version"], 1)
        self.assertEqual(data["report"], kind)
        self.assertEqual(data["note"], "Bookkeeping summary, not tax or accounting advice.")
        self.assertTrue(data["generator"].startswith("solo-books "))
        return data

    def business(self, data: dict, name: str) -> dict:
        return [b for b in data["businesses"] if b["business"] == name][0]

    def test_json_profit_and_loss(self) -> None:
        data = self.json_report("pnl")
        self.assertEqual([b["business"] for b in data["businesses"]], ["Business A", "Business B"])
        for key, name in (("business_a", "Business A"), ("business_b", "Business B")):
            wanted = self.expected[key]["pnl"]
            found = self.business(data, name)
            self.assertEqual(found["period"], {"label": "2025-Q1", "start": "2025-01-01", "end": "2025-03-31"})
            by_code = {c["code"]: c for c in found["categories"]}
            for code, amount in wanted.items():
                if code.isdigit():
                    shown = cents(by_code[code]["total"])
                    sign = -1 if by_code[code]["section"] == "returns" else 1
                    self.assertEqual(shown, sign * to_cents(amount), code)
            self.assertEqual(cents(found["totals"]["net"]["total"]), to_cents(wanted["net"]))
            self.assertEqual(cents(found["totals"]["total_income"]["values"][0]), to_cents(wanted["total_income"]))
            self.assertEqual(found["uncategorized"]["count"], 0)
        excluded = {c["code"]: cents(c["total"]) for c in self.business(data, "Business A")["not_income_or_expense"]}
        self.assertEqual(excluded, {"3100": -150000, "3900": 0})
        net = to_cents(self.expected["business_a"]["pnl"]["net"]) + to_cents(self.expected["business_b"]["pnl"]["net"])
        self.assertEqual(cents(data["all_businesses"]["net"]), net)

    def test_json_by_month_has_a_value_per_bucket(self) -> None:
        data = self.json_report("pnl", "--by", "month", "--business", "Business A")
        found = data["businesses"][0]
        self.assertEqual([b["label"] for b in found["buckets"]], ["2025-01", "2025-02", "2025-03"])
        months = self.expected["business_a"]["pnl_by_month"]
        self.assertEqual(
            [cents(v) for v in found["totals"]["net"]["values"]],
            [to_cents(months[m]["net"]) for m in ("2025-01", "2025-02", "2025-03")],
        )
        self.assertNotIn("all_businesses", data)

    def test_json_balances(self) -> None:
        data = self.json_report("balances")
        a = self.business(data, "Business A")
        accounts = {acct["name"]: acct for acct in a["accounts"]}
        self.assertEqual(cents(accounts["A Checking"]["end"]), 1192815)
        self.assertEqual(cents(accounts["A Card"]["end"]), 51062)
        self.assertEqual(accounts["A Card"]["shows"], "owed")
        self.assertEqual(cents(a["money_at_start"]), 480000)
        self.assertEqual(cents(a["net_profit"]), 811753)
        self.assertEqual(cents(a["owner_money"]), -150000)
        self.assertEqual(cents(a["money_at_end"]), 1141753)
        b = self.business(data, "Business B")
        self.assertEqual(cents(b["money_at_end"]), to_cents(self.expected["business_b"]["ending_balance"]))

    def test_json_schedule_c(self) -> None:
        data = self.json_report("schedule-c")
        for key, name in (("business_a", "Business A"), ("business_b", "Business B")):
            lines = {line["line"]: cents(line["amount"]) for line in self.business(data, name)["lines"]}
            for line, amount in self.expected[key]["schedule_c_2025"].items():
                self.assertEqual(lines[line], to_cents(amount), f"{name} line {line}")
        b = self.business(data, "Business B")
        self.assertEqual(cents(b["ties_to_profit_and_loss"]["inventory_change"]), 25000)
        self.assertIn("irs.gov", b["form"]["source_url"])

    def test_cashflow_matches_the_hand_workings(self) -> None:
        wanted = self.expected["cashflow_q1"]
        rows = parse_csv(self.ok(*REPORT_COMMANDS["cashflow"], "--format", "csv"))
        by_line = {row["line"]: row for row in rows if row["section"] in ("account", "what moved it") and row["business"] == "Business A"}
        by_line.update({row["line"]: row for row in rows if row["section"] == "account"})
        for account, figures in wanted["accounts"].items():
            row = by_line[account]
            self.assertEqual((row["money_in"], row["money_out"], row["net"]), (figures["in"], figures["out"], figures["net"]))
        data = self.json_report("cashflow")
        purposes = {line["label"]: line for line in self.business(data, "Business A")["what_moved_it"]}
        for label, figures in wanted["business_a"].items():
            found = purposes[label]
            self.assertEqual(
                (cents(found["money_in"]), cents(found["money_out"]), cents(found["net"])),
                (to_cents(figures["in"]), to_cents(figures["out"]), to_cents(figures["net"])),
            )
        self.assertIsNone(self.business(data, "Business A")["waiting_for_review"])
        self.assertEqual(cents(self.business(data, "Business A")["all_accounts"]["net"]), 661753)

    def test_cashflow_by_month_adds_up_to_the_quarter(self) -> None:
        data = self.json_report("cashflow", "--by", "month", "--business", "Business A")
        checking = [a for a in data["businesses"][0]["accounts"] if a["name"] == "A Checking"][0]
        self.assertEqual(len(checking["buckets"]), 3)
        self.assertEqual(sum(cents(b["net"]) for b in checking["buckets"]), 692815)
        self.assertEqual([cents(b["net"]) for b in checking["buckets"]], [318800, 216325, 157690])

    def test_categories_match_the_hand_workings(self) -> None:
        wanted = self.expected["categories_q1"]
        data = self.json_report("categories")
        for key, name in (("business_a", "Business A"), ("business_b", "Business B")):
            found = {c["code"]: c for c in self.business(data, name)["categories"]}
            for code, figures in wanted[key].items():
                stat = found[code]
                self.assertEqual(stat["rows"], figures["rows"], code)
                self.assertEqual(cents(stat["total"]), to_cents(figures["total"]), code)
                self.assertEqual(cents(stat["average"]), to_cents(figures["average"]), code)
                if "largest" in figures:
                    self.assertEqual(cents(stat["largest"]["amount"]), to_cents(figures["largest"]), code)
        meals = {c["code"]: c for c in self.business(data, "Business A")["categories"]}["6150"]
        self.assertIn("BLUE FERN CAFE", meals["largest"]["description"])
        row = self.conn.execute("SELECT amount_cents FROM transactions WHERE id = ?", (meals["largest"]["transaction_id"],)).fetchone()
        self.assertEqual(row["amount_cents"], -1840)

    def test_categories_csv_and_table(self) -> None:
        rows = parse_csv(self.ok(*REPORT_COMMANDS["categories"], "--business", "Business A", "--format", "csv"))
        travel = [r for r in rows if r["code"] == "6140"][0]
        self.assertEqual((travel["rows"], travel["total"], travel["average"], travel["largest_amount"]), ("3", "530.00", "176.67", "412.80"))
        self.assertEqual(rows[-1]["kind"], "note")
        text = self.ok(*REPORT_COMMANDS["categories"], "--business", "Business A")
        self.assertRegex(text, r"6140\s+Travel\s+3\s+530\.00\s+176\.67\s+412\.80")
        self.assertTrue(text.rstrip().endswith("not tax or accounting advice."))

    def test_trend_lays_years_side_by_side(self) -> None:
        data = self.json_report("trend", "--business", "Business A")
        self.assertEqual(data["years"], [2024, 2025])
        sales = [c for c in data["businesses"][0]["categories"] if c["code"] == "4000"][0]
        self.assertEqual([cents(v) for v in sales["values"]], [0, 1160050])
        self.assertEqual(cents(sales["change"]), 1160050)
        self.assertIsNone(sales["change_percent"])
        net = data["businesses"][0]["totals"]["net"]
        self.assertEqual([cents(v) for v in net["values"]], [0, 811753])
        rows = parse_csv(self.ok(*REPORT_COMMANDS["trend"], "--business", "Business A", "--format", "csv"))
        self.assertEqual(list(rows[0])[4:], ["2024", "2025", "change", "change_percent"])
        net_row = [r for r in rows if r["category"] == "Net profit or (loss)"][0]
        self.assertEqual((net_row["2024"], net_row["2025"], net_row["change"]), ("0.00", "8117.53", "8117.53"))

    def test_html_is_one_self_contained_page_for_every_report(self) -> None:
        for kind, command in REPORT_COMMANDS.items():
            page = self.ok(*command, "--format", "html")
            self.assertTrue(page.startswith("<!doctype html>"), kind)
            self.assertIn("Content-Security-Policy", page)
            self.assertIn("@media print", page)
            self.assertIn("Bookkeeping summary, not tax or accounting advice.", page)
            for loads_something in ("<script", "<link", "src=", "href=", "url(", "@import"):
                self.assertNotIn(loads_something, page, kind)
            self.assertEqual(page.count("<html"), 1)
            self.assertEqual(page.count("<table>"), page.count("</table>"))
        self.assertIn("8,117.53", self.ok(*REPORT_COMMANDS["pnl"], "--format", "html"))
        self.assertIn("4,982.25", self.ok(*REPORT_COMMANDS["schedule-c"], "--format", "html"))

    def test_markdown_tables(self) -> None:
        text = self.ok(*REPORT_COMMANDS["pnl"], "--business", "Business A", "--format", "markdown")
        self.assertTrue(text.startswith("# Profit and loss\n"))
        self.assertIn("## Profit and loss: Business A", text)
        self.assertIn("|---|---:|", text)
        self.assertIn("| **NET PROFIT OR \\(LOSS\\)** | **8,117.53** |", text)
        self.assertIn("**Bookkeeping summary, not tax or accounting advice.**", text)
        for kind, command in REPORT_COMMANDS.items():
            out = self.ok(*command, "--format", "markdown")
            for line in out.splitlines():
                if line.startswith("|"):
                    self.assertTrue(line.endswith("|"), f"{kind}: {line}")

    def test_files_get_a_byte_order_mark_only_where_it_helps(self) -> None:
        for name, wants_mark in (("table", True), ("csv", True), ("json", False), ("html", False), ("markdown", False)):
            path = self.dir / f"pnl.{name}"
            self.ok(*REPORT_COMMANDS["pnl"], "--format", name, "-o", str(path))
            self.assertEqual(path.read_bytes().startswith(b"\xef\xbb\xbf"), wants_mark, name)
        json.loads((self.dir / "pnl.json").read_text(encoding="utf-8"))

    def test_text_formats_tell_of_rows_waiting_for_review(self) -> None:
        acct = books.get_account(self.conn, "A Checking")
        ledger.add_manual(self.conn, acct, date(2025, 2, 3), -4200, "MYSTERY SHOP")
        for kind in ("pnl", "cashflow", "categories", "trend"):
            for name in ("table", "html", "markdown"):
                out = self.ok(*REPORT_COMMANDS[kind], "--business", "Business A", "--format", name)
                self.assertRegex(out, r"(?i)warning", f"{kind} {name}")
        data = self.json_report("cashflow", "--business", "Business A")
        waiting = data["businesses"][0]["waiting_for_review"]
        self.assertEqual((waiting["rows"], cents(waiting["money_out"])), (1, 4200))
        data = self.json_report("categories", "--business", "Business A")
        self.assertEqual(data["businesses"][0]["waiting_for_review"]["rows"], 1)
        self.assertIsNone(data["businesses"][0]["waiting_for_review"]["code"])
        data = self.json_report("trend", "--business", "Business A")
        self.assertEqual(data["businesses"][0]["uncategorized_rows"], {"2024": 0, "2025": 1})


class HostileText(BooksCase):
    """Names and descriptions come from bank files and typing. No format may let them act as markup or formulas."""

    NASTY_NAME = '<b onclick="x">Shop</b> & | *bold* [link](y)'
    NASTY_ROW = '=HYPERLINK("http://example.invalid","x") <script>alert(1)</script> | **'

    def setUp(self) -> None:
        super().setUp()
        self.biz = books.add_business(self.conn, self.NASTY_NAME)
        self.acct = books.add_account(self.conn, self.biz, "<i>Till</i>", "checking", "Example Bank", 0, date(2025, 1, 1))
        books.add_category(self.conn, self.biz["id"], "6400", "<img src=x onerror=alert(2)>", "expense")
        category = books.find_category(self.conn, self.biz["id"], "6400")
        ledger.add_manual(self.conn, self.acct, date(2025, 2, 1), -1234, self.NASTY_ROW, category)
        ledger.add_manual(self.conn, self.acct, date(2025, 2, 2), 5000, "+SUM(A1:A9) deposit", books.find_category(self.conn, self.biz["id"], "4000"))

    def test_html_escapes_every_value(self) -> None:
        for kind, command in REPORT_COMMANDS.items():
            page = self.ok(*command, "--format", "html")
            for raw in ("<b onclick", "<script>", "<img", "<i>Till"):
                self.assertNotIn(raw, page, kind)
            self.assertIn("&lt;b onclick=&quot;x&quot;&gt;Shop&lt;/b&gt; &amp;", page, kind)
        page = self.ok(*REPORT_COMMANDS["categories"], "--format", "html")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("&lt;img src=x onerror=alert(2)&gt;", page)

    def test_markdown_shows_text_as_written(self) -> None:
        out = self.ok(*REPORT_COMMANDS["categories"], "--format", "markdown")
        self.assertNotIn("<script>", out)
        self.assertIn("\\<script\\>alert\\(1\\)\\</script\\>", out)
        self.assertIn("\\<b onclick=\"x\"\\>Shop\\</b\\> \\& \\| \\*bold\\* \\[link\\]\\(y\\)", out)

    def test_markdown_table_cells_keep_their_count(self) -> None:
        out = self.ok(*REPORT_COMMANDS["categories"], "--format", "markdown")
        tables = [line for line in out.splitlines() if line.startswith("|")]
        widths = {len(re.findall(r"(?<!\\)\|", line)) for line in tables}
        self.assertEqual(widths, {9})

    def test_csv_neutralizes_formulas(self) -> None:
        rows = parse_csv(self.ok(*REPORT_COMMANDS["categories"], "--format", "csv"))
        descriptions = [r["largest_description"] for r in rows if r["largest_description"]]
        self.assertIn("'" + self.NASTY_ROW, descriptions)
        self.assertIn("'+SUM(A1:A9) deposit", descriptions)
        for kind in ("cashflow", "trend", "pnl", "balances"):
            for row in parse_csv(self.ok(*REPORT_COMMANDS[kind], "--format", "csv")):
                for cell in row.values():
                    if cell and cell[0] in "=+@" and not re.match(r"^\+\d+\.\d$", cell):
                        self.fail(f"{kind}: {cell!r} could run as a formula")

    def test_json_keeps_text_exactly(self) -> None:
        data = json.loads(self.ok(*REPORT_COMMANDS["categories"], "--format", "json"))
        found = {c["code"]: c for c in data["businesses"][0]["categories"]}
        self.assertEqual(found["6400"]["largest"]["description"], self.NASTY_ROW)
        self.assertEqual(data["businesses"][0]["business"], self.NASTY_NAME)


class Arithmetic(BooksCase):
    def test_averages_round_half_away_from_zero_in_whole_cents(self) -> None:
        self.assertEqual(analysis.divide_cents(1000, 3), 333)
        self.assertEqual(analysis.divide_cents(2000, 3), 667)
        self.assertEqual(analysis.divide_cents(5, 2), 3)
        self.assertEqual(analysis.divide_cents(-5, 2), -3)
        self.assertEqual(analysis.divide_cents(-1000, 3), -333)
        self.assertEqual(analysis.divide_cents(0, 7), 0)

    def test_percent_change(self) -> None:
        self.assertEqual(analysis.percent_change(1000, 1125), "+12.5")
        self.assertEqual(analysis.percent_change(1000, 1000), "0.0")
        self.assertEqual(analysis.percent_change(-2000, -1000), "+50.0")
        self.assertEqual(analysis.percent_change(3000, 1000), "-66.7")
        self.assertIsNone(analysis.percent_change(0, 500))

    def test_years(self) -> None:
        self.assertEqual(analysis.parse_years("2023..2025"), [2023, 2024, 2025])
        self.assertEqual(analysis.parse_years(" 2025 "), [2025])
        for bad in ("2025..2023", "23..25", "2025-2026", "2000..2030", "1800"):
            with self.assertRaises(Exception, msg=bad) as caught:
                analysis.parse_years(bad)
            self.assertEqual(type(caught.exception).__name__, "BooksError")
        self.business()
        self.assertIn("not a run of years", self.fails("report", "trend", "--years", "last year"))

    def test_trend_change_across_three_years(self) -> None:
        biz = self.business()
        acct = self.account(biz)
        meals = self.category(biz, "6150")
        for year, amount in ((2023, -10000), (2024, -12000), (2025, -11250)):
            ledger.add_manual(self.conn, acct, date(year, 6, 1), amount, f"LUNCH {year}", meals)
        trend = analysis.trend(self.conn, biz, [2023, 2024, 2025])
        self.assertEqual(trend.pnl.expenses[0].values, [10000, 12000, 11250])
        self.assertEqual(trend.change(trend.pnl.expenses[0].values), 1250)
        out = self.ok("report", "trend", "--years", "2023..2025")
        self.assertRegex(out, r"6150 Meals\s+100\.00\s+120\.00\s+112\.50\s+12\.50\s+\+12\.5")

    def test_cashflow_buckets_and_ties(self) -> None:
        biz = self.business()
        acct = self.account(biz)
        card = self.account(biz, "A Card", "credit_card")
        ledger.add_manual(self.conn, acct, date(2025, 1, 5), 10000, "SALE", self.category(biz, "4000"))
        ledger.add_manual(self.conn, acct, date(2025, 4, 5), -3000, "TO CARD", self.category(biz, "3900"))
        ledger.add_manual(self.conn, card, date(2025, 4, 6), 3000, "FROM CHECKING", self.category(biz, "3900"))
        ledger.add_manual(self.conn, card, date(2025, 5, 1), -700, "UNKNOWN")
        flow = analysis.cashflow(self.conn, biz, parse_period("2025"), "quarter")
        self.assertTrue(flow.ties)
        checking = [a for a in flow.accounts if a.label == "A Checking"][0]
        self.assertEqual((checking.money_in, checking.money_out), ([10000, 0, 0, 0], [0, 3000, 0, 0]))
        self.assertEqual(flow.waiting.money_out, [0, 700, 0, 0])
        self.assertEqual(flow.purposes[2].total_net, 0)
        self.assertEqual(flow.total.total_net, 10000 - 700)
        self.assertEqual(len(flow.warnings), 1)
