"""Reports, the preparer folder, and inventory counts."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List

from .. import books, ledger, render, reports
from ..errors import BooksError
from ..money import format_cents, parse_cents
from ..periods import parse_period, year_period
from ..text import slug
from .common import add_command, add_group, chosen_businesses, deliver, year_arg


def cmd_report_pnl(args, conn) -> int:
    period = parse_period(args.period)
    reports_list = [reports.profit_and_loss(conn, b, period, args.by) for b in chosen_businesses(conn, args.business)]
    if args.format == "csv":
        rows: List[List[str]] = []
        for pnl in reports_list:
            rows.extend(render.pnl_csv_rows(pnl))
        deliver(render.csv_text(render.PNL_CSV_HEADERS, rows), args.output)
        return 0
    chunks = [render.pnl_text(pnl) for pnl in reports_list]
    if len(reports_list) > 1:
        rows = [
            [
                pnl.business,
                format_cents(sum(pnl.total_income)),
                format_cents(sum(pnl.total_cogs)),
                format_cents(sum(pnl.total_expenses)),
                format_cents(sum(pnl.net)),
            ]
            for pnl in reports_list
        ]
        rows.append(
            [
                "All businesses",
                format_cents(sum(sum(p.total_income) for p in reports_list)),
                format_cents(sum(sum(p.total_cogs) for p in reports_list)),
                format_cents(sum(sum(p.total_expenses) for p in reports_list)),
                format_cents(sum(sum(p.net) for p in reports_list)),
            ]
        )
        chunks.append(
            f"All businesses, {period.label}\n"
            + render.table(["Business", "Income", "Cost of goods", "Expenses", "Net"], rows, {1, 2, 3, 4})
        )
    deliver("\n\n".join(chunks), args.output)
    return 0


def cmd_report_schedule_c(args, conn) -> int:
    businesses = chosen_businesses(conn, args.business)
    results = [reports.schedule_c(conn, b, args.year, args.lines_year) for b in businesses]
    if args.format == "csv":
        rows: List[List[str]] = []
        for result in results:
            rows.extend(render.schedule_c_csv_rows(result))
        deliver(render.csv_text(render.SCHEDULE_CSV_HEADERS, rows), args.output)
        return 0
    deliver("\n\n".join(render.schedule_c_text(r, args.all_lines) for r in results), args.output)
    return 0


def cmd_report_balances(args, conn) -> int:
    period = parse_period(args.period)
    reports_list = [reports.balances(conn, b, period) for b in chosen_businesses(conn, args.business)]
    if args.format == "csv":
        rows: List[List[str]] = []
        for report in reports_list:
            rows.extend(render.balances_csv_rows(report))
        deliver(render.csv_text(render.BALANCES_CSV_HEADERS, rows), args.output)
        return 0
    chunks = [render.balances_text(report) for report in reports_list]
    if len(reports_list) > 1:
        chunks.append(render.balances_summary_text(reports_list))
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
    for business in businesses:
        name = stems[business["id"]]
        result = reports.schedule_c(conn, business, args.year, args.lines_year)
        rollup = out_dir / f"{name}-schedule-c-{args.year}.csv"
        files.append((rollup, render.csv_text(render.SCHEDULE_CSV_HEADERS, render.schedule_c_csv_rows(result))))
        pnl = reports.profit_and_loss(conn, business, period, "quarter")
        pnl_path = out_dir / f"{name}-profit-and-loss-{args.year}.csv"
        files.append((pnl_path, render.csv_text(render.PNL_CSV_HEADERS, render.pnl_csv_rows(pnl))))
        rows = ledger.find_transactions(conn, business_id=business["id"], period=period)
        detail = out_dir / f"{name}-transactions-{args.year}.csv"
        files.append((detail, render.csv_text(render.DETAIL_CSV_HEADERS, render.detail_csv_rows(rows, result.form))))
        balances_path = out_dir / f"{name}-balances-{args.year}.csv"
        balances_rows = render.balances_csv_rows(reports.balances(conn, business, period))
        files.append((balances_path, render.csv_text(render.BALANCES_CSV_HEADERS, balances_rows)))
        summary.append(f"{business['name']}: line 31 {format_cents(result.net_line.cents)}, {len(rows)} rows")
        summary.append(f"  {rollup}\n  {pnl_path}\n  {balances_path}\n  {detail}")
        if result.uncategorized_count:
            summary.append(f"  WARNING: {result.uncategorized_count} uncategorized row(s) are not in the figures. Run 'review' first.")
        summary.extend(f"  WARNING: {warning}" for warning in result.warnings)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BooksError(f"cannot create the folder {out_dir}: {exc.strerror}") from None
    for path, text in files:
        render.deliver(text, str(path))
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
    report = add_group(commands, "report", "profit and loss, balances, Schedule C rollup, preparer export")
    p = add_command(report, "pnl", cmd_report_pnl, "profit and loss by period and business")
    p.add_argument("--period", required=True, help="2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20")
    p.add_argument("--by", choices=("total", "month", "quarter", "year"), default="total")
    p.add_argument("--business", help="default: every business")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(report, "balances", cmd_report_balances, "account balances at both ends of a period, and what moved the money")
    p.add_argument("--period", required=True, help="2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20")
    p.add_argument("--business", help="default: every business")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(report, "schedule-c", cmd_report_schedule_c, "roll one tax year up to Schedule C lines")
    p.add_argument("--year", type=year_arg, required=True)
    p.add_argument("--lines-year", type=year_arg, help="use another year's form lines, for a year with no data file")
    p.add_argument("--business", help="default: every business")
    p.add_argument("--all-lines", action="store_true", help="show lines with nothing on them too")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(report, "preparer", cmd_report_preparer, "write a folder of CSV files for a tax preparer")
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
