"""CSV for spreadsheets: one header row, plain numbers, and text that cannot run as a formula.

Notes, warnings and the not-advice line go in rows of their own at the end of each business, as they always have.
"""
from __future__ import annotations

from typing import List

from .. import render
from ..analysis import percent_change
from ..money import plain_cents
from ..reports import NOT_ADVICE
from ..text import spreadsheet_safe
from .base import Format, Report

CASHFLOW_CSV_HEADERS = ["business", "period", "period_start", "period_end", "section", "line", "money_in", "money_out", "net"]
CATEGORIES_CSV_HEADERS = [
    "business", "period_start", "period_end", "code", "category", "kind", "rows", "total", "average",
    "largest_amount", "largest_date", "largest_transaction_id", "largest_description",
]


def _rows_for(report: Report, make) -> List[List[str]]:
    rows: List[List[str]] = []
    for part in report.parts:
        rows.extend(make(part))
    return rows


def pnl_csv_rows(pnl, only: str) -> List[List[str]]:
    """Profit and loss rows, with a note row when the figures are one tag's rows."""
    rows = render.pnl_csv_rows(pnl)
    if only:
        period = pnl.period
        rows.append([pnl.business, period.label, period.start.isoformat(), period.end.isoformat(), "note", "", only, ""])
    return rows


def cashflow_csv_rows(part, only: str = "") -> List[List[str]]:
    rows: List[List[str]] = []

    def add(index: int, section: str, line) -> None:
        bucket = part.buckets[index]
        rows.append([
            part.business, bucket.label, bucket.start.isoformat(), bucket.end.isoformat(), section, line.label,
            plain_cents(line.money_in[index]), plain_cents(line.money_out[index]), plain_cents(line.net[index]),
        ])

    total = part.total
    for index in range(len(part.buckets)):
        for account in part.accounts:
            add(index, "account", account)
        add(index, "total", total)
        for purpose in part.purposes:
            add(index, "what moved it", purpose)
        if part.waiting:
            add(index, "what moved it", part.waiting)
    for note in [*([only] if only else []), *part.warnings, NOT_ADVICE]:
        rows.append([part.business, part.period.label, part.period.start.isoformat(), part.period.end.isoformat(), "note", note, "", "", ""])
    return rows


def trend_csv(report: Report) -> str:
    """The years side by side, as in the table, so the header names the years."""
    years = [str(year) for year in report.parts[0].years]
    headers = ["business", "section", "code", "category", *years, "change", "change_percent"]
    rows: List[List[str]] = []
    for part in report.parts:
        pnl = part.pnl

        def add(section: str, code: str, name: str, values: List[int]) -> None:
            change = values[-1] - values[0]
            rows.append([
                part.business, section, code, name, *(plain_cents(v) for v in values), plain_cents(change),
                percent_change(values[0], values[-1]) or "",
            ])

        for entry in pnl.income:
            add("income", entry.code, entry.name, entry.values)
        for entry in pnl.returns:
            add("income", entry.code, entry.name, [-v for v in entry.values])
        add("total", "", "Total income", pnl.total_income)
        for entry in pnl.cogs:
            add("cost of goods sold", entry.code, entry.name, entry.values)
        add("total", "", "Gross profit", pnl.gross_profit)
        for entry in pnl.expenses:
            add("expense", entry.code, entry.name, entry.values)
        add("total", "", "Total expenses", pnl.total_expenses)
        add("total", "", "Net profit or (loss)", pnl.net)
        for entry in pnl.off.excluded:
            add("not income or expense", entry.code, entry.name, entry.values)
        blank = [""] * (len(years) + 2)
        for year, count in zip(part.years, part.waiting):
            if count:
                rows.append([part.business, "note", "", f"{count} uncategorized rows in {year} are not in these figures", *blank])
        if report.only:
            rows.append([part.business, "note", "", report.only, *blank])
        rows.append([part.business, "note", "", NOT_ADVICE, *blank])
    return render.csv_text(headers, rows)


def categories_csv_rows(part, only: str = "") -> List[List[str]]:
    rows: List[List[str]] = []
    start, end = part.period.start.isoformat(), part.period.end.isoformat()
    for stat in part.categories + ([part.waiting] if part.waiting else []):
        rows.append([
            part.business, start, end, stat.code, stat.name, stat.kind or "uncategorized", str(stat.rows),
            plain_cents(stat.total), plain_cents(stat.average), plain_cents(stat.largest_amount), stat.largest_on,
            str(stat.largest_id), spreadsheet_safe(stat.largest_description),
        ])
    for note in [*([only] if only else []), NOT_ADVICE]:
        rows.append([part.business, start, end, "", note, "note", *[""] * 7])
    return rows


class CsvFormat(Format):
    name = "csv"
    help = "comma-separated values for a spreadsheet"
    byte_order_mark = True

    def render(self, report: Report) -> str:
        kind = report.kind
        if kind == "pnl":
            return render.csv_text(render.PNL_CSV_HEADERS, _rows_for(report, lambda pnl: pnl_csv_rows(pnl, report.only)))
        if kind == "balances":
            return render.csv_text(render.BALANCES_CSV_HEADERS, _rows_for(report, render.balances_csv_rows))
        if kind == "schedule-c":
            return render.csv_text(render.SCHEDULE_CSV_HEADERS, _rows_for(report, render.schedule_c_csv_rows))
        if kind == "cashflow":
            return render.csv_text(CASHFLOW_CSV_HEADERS, _rows_for(report, lambda part: cashflow_csv_rows(part, report.only)))
        if kind == "trend":
            return trend_csv(report)
        if kind == "categories":
            return render.csv_text(CATEGORIES_CSV_HEADERS, _rows_for(report, lambda part: categories_csv_rows(part, report.only)))
        if kind == "payees":
            return render.csv_text(
                render.PAYEES_CSV_HEADERS, _rows_for(report, lambda part: render.payees_csv_rows(part, report.limit, report.only))
            )
        if kind == "contractors":
            return render.csv_text(render.CONTRACTORS_CSV_HEADERS, _rows_for(report, render.contractors_csv_rows))
        return render.csv_text(render.BUDGET_CSV_HEADERS, _rows_for(report, lambda part: render.budget_csv_rows(part, report.only)))


FORMAT = CsvFormat()
