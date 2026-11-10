"""Reports, the preparer folder, and inventory counts."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List

from .. import analysis, books, formats, ledger, render, reports
from ..errors import BooksError
from ..formats import html_page, json_format, layout
from ..formats.base import Report
from ..money import format_cents, parse_cents
from ..periods import parse_period, year_period
from ..text import slug
from .common import add_command, add_group, chosen_businesses, deliver, year_arg

PERIOD_HELP = "2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20"
GROUPINGS = ("total", "month", "quarter", "year")


def _add_output(parser) -> None:
    parser.add_argument("--business", help="default: every business")
    parser.add_argument("--format", choices=formats.names(), default="table", help=formats.help_text())
    parser.add_argument("-o", "--output", help="write to this file instead of the screen")


def _write(report: Report, args) -> int:
    fmt = formats.get(args.format)
    deliver(fmt.render(report), args.output, fmt.byte_order_mark)
    return 0


def cmd_report_pnl(args, conn) -> int:
    period = parse_period(args.period)
    parts = [reports.profit_and_loss(conn, b, period, args.by) for b in chosen_businesses(conn, args.business)]
    return _write(Report("pnl", parts, period), args)


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
    business = books.get_business(conn, args.business)
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
