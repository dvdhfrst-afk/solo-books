"""Reports, the preparer folder, and inventory counts."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List

from .. import analysis, books, budgets, formats, ledger, payees, render, reports, tags
from ..errors import BooksError
from ..formats import html_page, json_format, layout
from ..formats.base import Report
from ..money import format_cents, parse_cents, plain_cents
from ..periods import parse_period, year_period
from ..text import slug
from .common import add_command, add_group, chosen_businesses, deliver, positive_arg, report_format, the_business, year_arg

PERIOD_HELP = "2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20"
GROUPINGS = ("total", "month", "quarter", "year")


def _add_output(parser) -> None:
    parser.add_argument("--business", help="default: every business")
    parser.add_argument(
        "--format", choices=formats.names(), help=f"{formats.help_text()}. Default table, or the report_format setting"
    )
    parser.add_argument("-o", "--output", help="write to this file instead of the screen")


def _write(report: Report, args) -> int:
    fmt = formats.get(report_format(args))
    deliver(fmt.render(report), args.output, fmt.byte_order_mark)
    return 0


def cmd_report_pnl(args, conn) -> int:
    period = parse_period(args.period)
    chosen = chosen_businesses(conn, args.business)
    tagged: Dict[int, sqlite3.Row] = {}
    if args.tag:
        tagged = tags.tags_named(conn, args.tag, chosen[0]["id"] if args.business else None)
        chosen = [business for business in chosen if business["id"] in tagged]
    parts = [reports.profit_and_loss(conn, b, period, args.by, tagged[b["id"]]["id"] if tagged else None) for b in chosen]
    tag_name = tagged[chosen[0]["id"]]["name"] if tagged else None
    return _write(Report("pnl", parts, period, tag=tag_name), args)


def cmd_report_schedule_c(args, conn) -> int:
    parts = [reports.schedule_c(conn, b, args.year, args.lines_year) for b in chosen_businesses(conn, args.business)]
    return _write(Report("schedule-c", parts, year_period(args.year), all_lines=args.all_lines), args)


def cmd_report_balances(args, conn) -> int:
    period = parse_period(args.period)
    parts = [reports.balances(conn, b, period) for b in chosen_businesses(conn, args.business)]
    return _write(Report("balances", parts, period), args)


def cmd_report_cashflow(args, conn) -> int:
    period = parse_period(args.period)
    parts = [analysis.cashflow(conn, b, period, args.by) for b in chosen_businesses(conn, args.business)]
    return _write(Report("cashflow", parts, period), args)


def cmd_report_trend(args, conn) -> int:
    years = analysis.parse_years(args.years)
    parts = [analysis.trend(conn, b, years) for b in chosen_businesses(conn, args.business)]
    return _write(Report("trend", parts), args)


def cmd_report_categories(args, conn) -> int:
    period = parse_period(args.period)
    parts = [analysis.category_stats(conn, b, period) for b in chosen_businesses(conn, args.business)]
    return _write(Report("categories", parts, period), args)


def _waiting_text(count: int, money_in: int, money_out: int, show=format_cents) -> str:
    """The same words profit and loss uses for rows still in the queue."""
    return f"{count} uncategorized row(s) are NOT in these figures (in {show(money_in)}, out {show(money_out)}). Run 'review'."


PAYEES_CSV_HEADERS = ["business", "period", "section", "rank", "payee", "rows", "money_out", "money_in", "paid_by_card"]


def cmd_report_payees(args, conn) -> int:
    period = year_period(args.year)
    found = [payees.payee_report(conn, b, period) for b in chosen_businesses(conn, args.business)]
    if args.format == "csv":
        rows: List[List[str]] = []
        for report in found:
            for section, direction in (("money out", "out"), ("money in", "in")):
                for rank, entry in enumerate(payees.top(report, direction, args.limit), start=1):
                    rows.append([
                        report.business, period.label, section, str(rank), entry.payee.name, str(entry.rows),
                        plain_cents(entry.money_out), plain_cents(entry.money_in), plain_cents(entry.by_card),
                    ])
            if report.waiting_count:
                waiting = _waiting_text(report.waiting_count, report.waiting_in, report.waiting_out, plain_cents)
                rows.append([report.business, period.label, "note", "", waiting, "", "", "", ""])
            rows.append([report.business, period.label, "note", "", reports.NOT_ADVICE, "", "", "", ""])
        deliver(render.csv_text(PAYEES_CSV_HEADERS, rows), args.output)
        return 0
    chunks = []
    for report in found:
        out_rows = [
            [str(rank), e.payee.name, str(e.rows), format_cents(e.money_out), format_cents(e.by_card), format_cents(e.money_in)]
            for rank, e in enumerate(payees.top(report, "out", args.limit), start=1)
        ]
        in_rows = [
            [str(rank), e.payee.name, str(e.rows), format_cents(e.money_in), format_cents(e.money_out)]
            for rank, e in enumerate(payees.top(report, "in", args.limit), start=1)
        ]
        lines = [
            f"Payees: {report.business}, {period.label}",
            "Rows booked to income and expense categories, from every account."
            + (f" {report.left_out_count} transfer and owner row(s) are not payments to anyone and are left out." if report.left_out_count else ""),
            "",
            f"Money out, top {args.limit}:",
            render.table(["#", "Payee", "Rows", "Money out", "By card", "Money in"], out_rows, {0, 2, 3, 4, 5}),
            "",
            f"Money in, top {args.limit}:",
            render.table(["#", "Payee", "Rows", "Money in", "Money out"], in_rows, {0, 2, 3, 4}),
            "",
        ]
        if report.waiting_count:
            lines.append("WARNING: " + _waiting_text(report.waiting_count, report.waiting_in, report.waiting_out))
        lines.append(reports.NOT_ADVICE)
        chunks.append("\n".join(lines))
    deliver("\n\n".join(chunks), args.output)
    return 0


CONTRACTORS_NOTICE_LINES = (
    "This is a bookkeeping list to hand your preparer. It is not a determination of who needs",
    "an information return or any other form; your preparer decides that.",
)
CONTRACTORS_NOTICE = " ".join(CONTRACTORS_NOTICE_LINES)
# The preparer folder divides its list of flagged payees here too.
DEFAULT_THRESHOLD = "600.00"
CONTRACTORS_CSV_HEADERS = [
    "business", "year", "threshold", "at_or_over", "payee", "spellings", "rows", "paid", "refunded", "net_paid", "paid_by_card",
]


def _contractor_cells(entry) -> List[str]:
    return [
        entry.payee.name, "; ".join(entry.payee.merchants), str(entry.rows), entry.money_out, entry.money_in,
        entry.net_paid, entry.by_card,
    ]


def _contractor_csv_rows(report: payees.ContractorReport) -> List[List[str]]:
    start = [report.business, str(report.period.start.year), plain_cents(report.threshold)]
    rows = []
    for over, entries in (("yes", report.at_or_over), ("no", report.under)):
        for entry in entries:
            cells = _contractor_cells(entry)
            rows.append([*start, over, *cells[:3], *[plain_cents(cents) for cents in cells[3:]]])
    notes = [CONTRACTORS_NOTICE, reports.NOT_ADVICE]
    if report.waiting_count:
        notes.insert(0, f"{report.waiting_count} row(s) to contractors are waiting for review and are not counted "
                        f"(out {plain_cents(report.waiting_out)}).")
    rows.extend([*start, "note", note, "", "", "", "", "", ""] for note in notes)
    return rows


def cmd_report_contractors(args, conn) -> int:
    period = year_period(args.year)
    threshold = parse_cents(args.threshold)
    found = [payees.contractor_report(conn, b, period, threshold) for b in chosen_businesses(conn, args.business)]
    if args.format == "csv":
        rows: List[List[str]] = []
        for report in found:
            rows.extend(_contractor_csv_rows(report))
        deliver(render.csv_text(CONTRACTORS_CSV_HEADERS, rows), args.output)
        return 0
    chunks = []
    headers = ["Payee", "Spellings", "Rows", "Paid", "Refunded", "Net paid", "Paid by card"]

    def table_of(entries) -> str:
        cells = [_contractor_cells(entry) for entry in entries]
        return render.table(headers, [[*c[:3], *[format_cents(cents) for cents in c[3:]]] for c in cells], {2, 3, 4, 5, 6})

    for report in found:
        lines = [
            f"Payments to contractors: {report.business}, {args.year}",
            "Payees flagged with 'payee set NAME --contractor yes'. Net paid is money out less refunds, booked to income",
            "and expense categories, from every account, cards included.",
            *CONTRACTORS_NOTICE_LINES,
            "",
        ]
        if not report.flagged:
            lines.append("No payee of this business is flagged as a contractor.")
        else:
            lines.append(f"Net paid of {format_cents(threshold)} or more: {len(report.at_or_over)} payee(s)")
            if report.at_or_over:
                lines.append(table_of(report.at_or_over))
            lines.extend(["", f"Under {format_cents(threshold)}, or nothing paid this year: {len(report.under)} payee(s)"])
            if report.under:
                lines.append(table_of(report.under))
        lines.append("")
        if report.waiting_count:
            lines.append(
                f"WARNING: {report.waiting_count} row(s) to contractors are waiting for review and are NOT counted "
                f"(out {format_cents(report.waiting_out)}). Run 'review'."
            )
        lines.append(reports.NOT_ADVICE)
        chunks.append("\n".join(lines))
    deliver("\n\n".join(chunks), args.output)
    return 0


BUDGET_CSV_HEADERS = [
    "business", "period", "period_start", "period_end", "section", "code", "category", "budget", "actual", "difference",
    "percent_used",
]


def _budget_sections(report: budgets.BudgetReport):
    """Each section's lines and total, in the order profit and loss shows them."""
    sections = [("income", "INCOME", report.income, "Total income", report.total_income)]
    if report.cogs:
        sections.append(("cost of goods sold", "COST OF GOODS SOLD", report.cogs, "Total cost of goods sold", report.total_cogs))
    sections.append(("expense", "EXPENSES", report.expenses, "Total expenses", report.total_expenses))
    return sections


def cmd_report_budget(args, conn) -> int:
    period = parse_period(args.period)
    found = [budgets.budget_report(conn, b, period) for b in chosen_businesses(conn, args.business)]
    if args.format == "csv":
        rows: List[List[str]] = []
        for report in found:
            start = [report.business, period.label, period.start.isoformat(), period.end.isoformat()]

            def money(section: str, code: str, name: str, budget: int, actual: int) -> List[str]:
                used = budgets.percent_used(actual, budget) or ""
                return [*start, section, code, name, plain_cents(budget), plain_cents(actual), plain_cents(actual - budget), used.rstrip("%")]

            for section, _, lines, total_name, total in _budget_sections(report):
                rows.extend(money(section, line.code, line.name, line.budget, line.actual) for line in lines)
                rows.append(money("total", "", total_name, *total))
            rows.append(money("total", "", "Net profit or (loss)", *report.net))
            notes = [*report.warnings, "Difference is actual minus budget.", reports.NOT_ADVICE]
            if report.off.uncategorized_count:
                off = report.off
                notes.insert(0, _waiting_text(off.uncategorized_count, off.uncategorized_in, off.uncategorized_out, plain_cents))
            rows.extend([*start, "note", "", note, "", "", "", ""] for note in notes)
        deliver(render.csv_text(BUDGET_CSV_HEADERS, rows), args.output)
        return 0
    chunks = []
    for report in found:
        body: List[List[str]] = []

        def line_of(label: str, budget: int, actual: int) -> List[str]:
            used = budgets.percent_used(actual, budget) or "-"
            return [label, format_cents(budget), format_cents(actual), format_cents(actual - budget), used]

        for _, title, lines, total_name, total in _budget_sections(report):
            body.append([title, "", "", "", ""])
            body.extend(line_of(f"  {line.code} {line.name}", line.budget, line.actual) for line in lines)
            body.append(line_of(total_name, *total))
        body.append(line_of("NET PROFIT OR (LOSS)", *report.net))
        text = [
            f"Budget: {report.business}",
            f"Period: {period.label} ({period.start} to {period.end}), cash basis",
            "",
            render.table(["", "Budget", "Actual", "Difference", "Used"], body, {1, 2, 3, 4}),
            "",
            "Difference is actual minus budget: above zero is more income, or more spending, than planned.",
        ]
        text.extend(f"NOTE: {warning}" for warning in report.warnings)
        if report.off.uncategorized_count:
            off = report.off
            text.append("WARNING: " + _waiting_text(off.uncategorized_count, off.uncategorized_in, off.uncategorized_out))
        text.append(reports.NOT_ADVICE)
        chunks.append("\n".join(text))
    deliver("\n\n".join(chunks), args.output)
    return 0


def _file_stems(businesses: List[sqlite3.Row]) -> Dict[int, str]:
    """A file name per business. Two names can reduce to one slug, and neither may overwrite the other."""
    stems: Dict[int, str] = {}
    taken = set()
    for business in businesses:
        stem = slug(business["name"])
        if stem in taken:
            stem = f"{stem}-{business['id']}"
        taken.add(stem)
        stems[business["id"]] = stem
    return stems


def cmd_report_preparer(args, conn) -> int:
    """One folder a tax preparer can work from: the rollup, the quarters, the balances, and every row behind them."""
    out_dir = Path(args.out)
    if out_dir.exists() and not out_dir.is_dir():
        raise BooksError(f"{out_dir} is a file. Give a folder for --out.")
    period = year_period(args.year)
    businesses = chosen_businesses(conn, args.business)
    stems = _file_stems(businesses)
    # Work everything out first, so a report that cannot be made leaves no half-filled folder.
    files = []
    summary: List[str] = []
    pnls, rollups, balances_list = [], [], []
    for business in businesses:
        name = stems[business["id"]]
        result = reports.schedule_c(conn, business, args.year, args.lines_year)
        rollups.append(result)
        rollup = out_dir / f"{name}-schedule-c-{args.year}.csv"
        files.append((rollup, render.csv_text(render.SCHEDULE_CSV_HEADERS, render.schedule_c_csv_rows(result)), True))
        pnl = reports.profit_and_loss(conn, business, period, "quarter")
        pnls.append(pnl)
        pnl_path = out_dir / f"{name}-profit-and-loss-{args.year}.csv"
        files.append((pnl_path, render.csv_text(render.PNL_CSV_HEADERS, render.pnl_csv_rows(pnl)), True))
        rows = ledger.find_transactions(conn, business_id=business["id"], period=period)
        detail = out_dir / f"{name}-transactions-{args.year}.csv"
        files.append((detail, render.csv_text(render.DETAIL_CSV_HEADERS, render.detail_csv_rows(rows, result.form)), True))
        balances_path = out_dir / f"{name}-balances-{args.year}.csv"
        balances = reports.balances(conn, business, period)
        balances_list.append(balances)
        files.append((balances_path, render.csv_text(render.BALANCES_CSV_HEADERS, render.balances_csv_rows(balances)), True))
        summary.append(f"{business['name']}: line 31 {format_cents(result.net_line.cents)}, {len(rows)} rows")
        summary.append(f"  {rollup}\n  {pnl_path}\n  {balances_path}\n  {detail}")
        contractors = payees.contractor_report(conn, business, period, parse_cents(DEFAULT_THRESHOLD))
        if contractors.flagged:
            contractors_path = out_dir / f"{name}-contractors-{args.year}.csv"
            files.append((contractors_path, render.csv_text(CONTRACTORS_CSV_HEADERS, _contractor_csv_rows(contractors)), True))
            summary.append(f"  {contractors_path}")
        if result.uncategorized_count:
            summary.append(f"  WARNING: {result.uncategorized_count} uncategorized row(s) are not in the figures. Run 'review' first.")
        summary.extend(f"  WARNING: {warning}" for warning in result.warnings)
    # The year's three reports for every business, on one printable page and in one JSON file.
    year_reports = [
        Report("pnl", pnls, period),
        Report("schedule-c", rollups, period),
        Report("balances", balances_list, period),
    ]
    page_path, json_path = out_dir / "summary.html", out_dir / "summary.json"
    page = html_page.page(f"Preparer summary, {args.year}", [layout.document(report) for report in year_reports])
    files.append((page_path, page, False))
    files.append((json_path, json_format.bundle(args.year, year_reports), False))
    summary.append(f"Summary of every business: {page_path} and {json_path}")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BooksError(f"cannot create the folder {out_dir}: {exc.strerror}") from None
    for path, text, byte_order_mark in files:
        render.deliver(text, str(path), byte_order_mark)
    for line in summary:
        print(line)
    print(reports.NOT_ADVICE)
    return 0


def cmd_inventory_set(args, conn) -> int:
    business = the_business(conn, args)
    books.set_inventory(conn, business["id"], args.year, parse_cents(args.beginning), parse_cents(args.ending))
    print(f"Inventory counts saved for {business['name']}, {args.year}.")
    return 0


def cmd_inventory_list(args, conn) -> int:
    business_id = books.get_business(conn, args.business)["id"] if args.business else None
    rows = [
        [
            row["business_name"],
            str(row["tax_year"]),
            format_cents(row["beginning_cents"]),
            format_cents(row["ending_cents"]),
            format_cents(row["ending_cents"] - row["beginning_cents"]),
        ]
        for row in books.list_inventory(conn, business_id)
    ]
    print(render.table(["Business", "Year", "Beginning", "Ending", "Change"], rows, {1, 2, 3, 4}))
    return 0


def register(commands) -> None:
    report = add_group(
        commands, "report", "profit and loss, balances, Schedule C, cash flow, trend, categories, preparer folder"
    )
    p = add_command(report, "pnl", cmd_report_pnl, "profit and loss by period and business")
    p.add_argument("--period", required=True, help=PERIOD_HELP)
    p.add_argument("--by", choices=GROUPINGS, default="total")
    p.add_argument("--tag", help="only rows carrying this tag")
    _add_output(p)
    p = add_command(report, "balances", cmd_report_balances, "account balances at both ends of a period, and what moved the money")
    p.add_argument("--period", required=True, help=PERIOD_HELP)
    _add_output(p)
    p = add_command(report, "schedule-c", cmd_report_schedule_c, "roll one tax year up to Schedule C lines")
    p.add_argument("--year", type=year_arg, required=True)
    p.add_argument("--lines-year", type=year_arg, help="use another year's form lines, for a year with no data file")
    p.add_argument("--all-lines", action="store_true", help="show lines with nothing on them too")
    _add_output(p)
    p = add_command(
        report, "cashflow", cmd_report_cashflow, "money in and out of each account, and what moved it, from the bank rows"
    )
    p.add_argument("--period", required=True, help=PERIOD_HELP)
    p.add_argument("--by", choices=GROUPINGS, default="total")
    _add_output(p)
    p = add_command(report, "trend", cmd_report_trend, "each category's total per year side by side, with the change")
    p.add_argument("--years", required=True, help="2025, or a run of years such as 2023..2025")
    _add_output(p)
    p = add_command(report, "categories", cmd_report_categories, "each category's row count, total, average and largest row")
    p.add_argument("--period", required=True, help=PERIOD_HELP)
    _add_output(p)
    p = add_command(report, "payees", cmd_report_payees, "the payees money went to and came from most in a year")
    p.add_argument("--year", type=year_arg, required=True)
    p.add_argument("--business", help="default: every business")
    p.add_argument("--limit", type=positive_arg, default=10, help="how many payees each way (default 10)")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(
        report, "contractors", cmd_report_contractors,
        "payments in a year to payees flagged as contractors, as a list for your preparer",
    )
    p.add_argument("--year", type=year_arg, required=True)
    p.add_argument("--threshold", default=DEFAULT_THRESHOLD, help=f"divide the list at this net amount paid (default {DEFAULT_THRESHOLD})")
    p.add_argument("--business", help="default: every business")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(report, "budget", cmd_report_budget, "budget against actual per category for a year, quarter or month")
    p.add_argument("--period", required=True, help="2025, 2025-Q2, 2025-03, or a range of whole months")
    p.add_argument("--business", help="default: every business")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(
        report, "preparer", cmd_report_preparer, "write a folder of CSV files for a tax preparer, and a one-page summary"
    )
    p.add_argument("--year", type=year_arg, required=True)
    p.add_argument("--out", required=True, help="folder to write into")
    p.add_argument("--lines-year", type=year_arg, help="use another year's form lines, for a year with no data file")
    p.add_argument("--business", help="default: every business")

    inventory = add_group(commands, "inventory", "beginning and ending inventory for cost of goods sold")
    p = add_command(inventory, "set", cmd_inventory_set, "save a year's two counts")
    p.add_argument("--year", type=year_arg, required=True)
    p.add_argument("--beginning", required=True)
    p.add_argument("--ending", required=True)
    p.add_argument("--business")
    p = add_command(inventory, "list", cmd_inventory_list, "show the counts saved so far")
    p.add_argument("--business")
