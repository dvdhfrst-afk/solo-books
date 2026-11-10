"""Turning report objects into text tables and CSV rows."""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

from .files import write_atomic
from .money import format_cents, plain_cents
from .reconcile import ReconcileReport, printed
from .reports import NOT_ADVICE, Balances, Pnl, PnlRow, ScheduleC, ScheduleLine
from .text import spreadsheet_cell, spreadsheet_safe

_MAX_CELL = 46


def table(
    headers: Sequence[str], rows: Iterable[Sequence[str]], right: Optional[Set[int]] = None, max_cell: int = _MAX_CELL
) -> str:
    """A plain aligned table. Columns in right are right-aligned."""
    right = right or set()
    body = [[_clip(str(cell), max_cell) for cell in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in body:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: Sequence[str]) -> str:
        parts = [
            cell.rjust(widths[index]) if index in right else cell.ljust(widths[index])
            for index, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    out = [line(headers), line(["-" * w for w in widths])]
    out.extend(line(row) for row in body)
    return "\n".join(out)


def _clip(text: str, limit: int = _MAX_CELL) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def csv_text(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        # Text from a bank or a name typed long ago must not run as a formula. Numbers stay numbers.
        writer.writerow([spreadsheet_cell(cell) for cell in row])
    return out.getvalue()


def deliver(text: str, output: Optional[str], byte_order_mark: bool = True) -> None:
    """Write to a file (with a byte order mark so spreadsheets read accents right) or to the screen.

    JSON readers refuse a byte order mark, so formats like that pass byte_order_mark=False.
    """
    if output and not byte_order_mark:
        write_atomic(Path(output), text.encode("utf-8"))
    elif output:
        write_atomic(Path(output), ("\ufeff" + text).encode("utf-8"))
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


# Profit and loss

def pnl_text(pnl: Pnl) -> str:
    headers = ["", *[bucket.label for bucket in pnl.buckets]]
    multi = len(pnl.buckets) > 1
    if multi:
        headers.append("Total")
    right = set(range(1, len(headers)))

    def row(label: str, values: List[int]) -> List[str]:
        cells = [format_cents(v) for v in values]
        if multi:
            cells.append(format_cents(sum(values)))
        return [label, *cells]

    def categories(rows: List[PnlRow], negate: bool = False) -> List[List[str]]:
        result = []
        for entry in rows:
            values = [-v for v in entry.values] if negate else entry.values
            result.append(row(f"  {entry.code} {entry.name}", values))
        return result

    blank = [""] * len(headers)
    body: List[List[str]] = [["INCOME", *blank[1:]]]
    body += categories(pnl.income)
    body += categories(pnl.returns, negate=True)
    body.append(row("Total income", pnl.total_income))
    if pnl.cogs:
        body.append(["COST OF GOODS SOLD", *blank[1:]])
        body += categories(pnl.cogs)
        body.append(row("Gross profit", pnl.gross_profit))
    body.append(["EXPENSES", *blank[1:]])
    body += categories(pnl.expenses)
    body.append(row("Total expenses", pnl.total_expenses))
    body.append(row("NET PROFIT OR (LOSS)", pnl.net))

    lines = [
        f"Profit and loss: {pnl.business}",
        f"Period: {pnl.period.label} ({pnl.period.start} to {pnl.period.end}), cash basis",
        "",
        table(headers, body, right),
        "",
    ]
    lines.extend(pnl_off_lines(pnl))
    lines.append(NOT_ADVICE)
    return "\n".join(lines)


def pnl_off_lines(pnl: Pnl) -> List[str]:
    lines: List[str] = []
    off = pnl.off
    if off.uncategorized_count:
        lines.append(
            f"WARNING: {off.uncategorized_count} uncategorized row(s) are NOT in these figures "
            f"(in {format_cents(off.uncategorized_in)}, out {format_cents(off.uncategorized_out)}). Run 'review'."
        )
    if off.excluded:
        lines.append("Not income or expense (net money in, negative for money out):")
        for entry in off.excluded:
            lines.append(f"  {entry.code} {entry.name}: {format_cents(entry.total)}")
    return lines


PNL_CSV_HEADERS = ["business", "period", "period_start", "period_end", "section", "code", "category", "amount"]


def pnl_csv_rows(pnl: Pnl) -> List[List[str]]:
    rows: List[List[str]] = []

    def add(section: str, code: str, name: str, values: List[int]) -> None:
        for bucket, value in zip(pnl.buckets, values):
            rows.append(
                [
                    pnl.business,
                    bucket.label,
                    bucket.start.isoformat(),
                    bucket.end.isoformat(),
                    section,
                    code,
                    name,
                    plain_cents(value),
                ]
            )

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
    if pnl.off.uncategorized_count:
        rows.append(
            [pnl.business, pnl.period.label, pnl.period.start.isoformat(), pnl.period.end.isoformat(), "note", "", (
                f"{pnl.off.uncategorized_count} uncategorized rows are not in these figures "
                f"(in {plain_cents(pnl.off.uncategorized_in)}, out {plain_cents(pnl.off.uncategorized_out)})"
            ), ""]
        )
    rows.append([pnl.business, pnl.period.label, pnl.period.start.isoformat(), pnl.period.end.isoformat(), "note", "", NOT_ADVICE, ""])
    return rows


# Schedule C

_PART_TITLES = {
    "I": "Part I: Income",
    "II": "Part II: Expenses",
    "III": "Part III: Cost of Goods Sold",
    "V": "Part V: Other Expenses",
}


def schedule_c_text(report: ScheduleC, all_lines: bool = False) -> str:
    lines = [
        f"Schedule C rollup: {report.business}, tax year {report.tax_year}",
        f"{NOT_ADVICE} Hand this to your preparer with the transaction detail.",
        f"Lines from: {report.form.stamp()}",
    ]
    current_part = None
    rows: List[List[str]] = []
    for entry in report.lines:
        if not (all_lines or entry.computed or entry.cents != 0):
            continue
        if entry.entry["part"] != current_part:
            current_part = entry.entry["part"]
            rows.append(["", _PART_TITLES.get(current_part, f"Part {current_part}"), ""])
        rows.append([entry.line, entry.label, format_cents(entry.cents)])
        if entry.categories and (len(entry.categories) > 1 or entry.line == "48"):
            for category in entry.categories:
                rows.append(["", f"  {category.code} {category.name}", format_cents(category.total)])
    lines.extend(["", _wide_table(["Line", "Description", "Amount"], rows), ""])
    lines.extend(_schedule_notes(report))
    return "\n".join(lines)


def _wide_table(headers: List[str], rows: List[List[str]]) -> str:
    """Form line names are long and must not be clipped."""
    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    out = [f"{headers[0]:<{widths[0]}}  {headers[1]:<{widths[1]}}  {headers[2]:>{widths[2]}}"]
    out.append("  ".join("-" * w for w in widths))
    for row in rows:
        out.append(f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:>{widths[2]}}".rstrip())
    return "\n".join(out)


def _schedule_notes(report: ScheduleC) -> List[str]:
    notes: List[str] = []
    if report.uncategorized_count:
        notes.append(
            f"WARNING: {report.uncategorized_count} uncategorized row(s) are NOT in these figures "
            f"(in {format_cents(report.uncategorized_in)}, out {format_cents(report.uncategorized_out)}). Run 'review'."
        )
    notes.extend(f"WARNING: {warning}" for warning in report.warnings)
    for entry in report.lines:
        if entry.cents != 0 and entry.entry.get("note"):
            notes.append(f"Line {entry.line}: {entry.entry['note']}")
    notes.append(
        f"Ties to profit and loss: net {format_cents(report.pnl_net)}"
        f" + inventory change {format_cents(report.inventory_change)}"
        f" - left off the form {format_cents(report.off_form_net)}"
        f" = line 31 {format_cents(report.net_line.cents)}."
    )
    return notes


SCHEDULE_CSV_HEADERS = ["tax_year", "business", "part", "line", "description", "amount", "categories", "note"]


def _categories_cell(entry: ScheduleLine) -> str:
    return "; ".join(f"{c.code} {c.name}: {plain_cents(c.total)}" for c in entry.categories)


def schedule_c_csv_rows(report: ScheduleC) -> List[List[str]]:
    rows = [
        [
            str(report.tax_year),
            report.business,
            entry.entry["part"],
            entry.line,
            entry.label,
            plain_cents(entry.cents),
            _categories_cell(entry),
            entry.entry.get("note", "") if entry.cents != 0 else "",
        ]
        for entry in report.lines
    ]
    for note in [NOT_ADVICE, f"Lines from: {report.form.stamp()}", *_schedule_notes(report)]:
        rows.append([str(report.tax_year), report.business, "note", "", note, "", "", ""])
    return rows


DETAIL_CSV_HEADERS = [
    "transaction_id",
    "date",
    "business",
    "account",
    "amount",
    "description",
    "memo",
    "category_code",
    "category",
    "schedule_c_line",
    "booked_by",
    "reconciled",
    "note",
]


def detail_csv_rows(rows: Iterable, form) -> List[List[str]]:
    result = []
    for row in rows:
        key = row["schedule_c_key"]
        entry = form.find_by_key(key) if key else None
        result.append(
            [
                str(row["id"]),
                row["posted_on"],
                row["business_name"],
                row["account_name"],
                plain_cents(row["amount_cents"]),
                spreadsheet_safe(row["description"]),
                spreadsheet_safe(row["memo"]),
                row["category_code"] or "",
                row["category_name"] or "UNCATEGORIZED",
                entry["line"] if entry else "",
                row["category_source"] or "",
                "yes" if row["reconciliation_id"] else "no",
                spreadsheet_safe(row["note"]),
            ]
        )
    return result


# Balances

def _balance_lines(report: Balances) -> List[List[str]]:
    """The period's money, from the start to the end, as label and signed amount."""
    rows = [["Money at the start, less what cards were owed", format_cents(report.money_begin)]]
    if report.money_opened:
        rows.append(["Accounts opened in this period", format_cents(report.money_opened)])
    rows.append(["Net profit or (loss)", format_cents(report.profit)])
    rows.extend([f"{entry.code} {entry.name}", format_cents(entry.total)] for entry in report.off_profit)
    if report.waiting_count:
        rows.append([f"Waiting for review, {report.waiting_count} row(s)", format_cents(report.waiting_net)])
    rows.append(["Money at the end, less what cards are owed", format_cents(report.money_end)])
    return rows


def balances_text(report: Balances) -> str:
    accounts = [
        [a.name, a.kind, format_cents(a.printed(a.begin)), format_cents(a.printed(a.end))] for a in report.accounts
    ]
    lines = [
        f"Balances: {report.business}",
        f"Period: {report.period.label} ({report.period.start} to {report.period.end}), cash basis",
        "",
        table(["Account", "Kind", "Start", "End"], accounts, {2, 3}),
    ]
    if any(account.kind == "credit_card" for account in report.accounts):
        lines.append("Card balances show what is owed.")
    lines.extend(["", table(["What moved the money", "Amount"], _balance_lines(report), {1})])
    if report.inventory_end is not None:
        lines.extend(
            [
                "",
                f"Inventory count at {report.period.end}: {format_cents(report.inventory_end)} (from 'inventory set').",
                f"Money plus inventory: {format_cents(report.money_end + report.inventory_end)}.",
            ]
        )
    lines.append("")
    lines.extend(f"WARNING: {warning}" for warning in report.warnings)
    lines.append(NOT_ADVICE)
    return "\n".join(lines)


def balances_summary_text(reports_list: List[Balances]) -> str:
    def row(name: str, parts: List[Balances]) -> List[str]:
        return [
            name,
            format_cents(sum(p.money_begin + p.money_opened for p in parts)),
            format_cents(sum(p.profit for p in parts)),
            format_cents(sum(p.owner_net for p in parts)),
            format_cents(sum(p.transfers_net for p in parts)),
            format_cents(sum(p.waiting_net for p in parts)),
            format_cents(sum(p.money_end for p in parts)),
        ]

    rows = [row(report.business, [report]) for report in reports_list]
    rows.append(row("All businesses", reports_list))
    return f"All businesses, {reports_list[0].period.label}\n" + table(
        ["Business", "Start", "Profit", "Owner money", "Transfers", "Waiting", "End"], rows, {1, 2, 3, 4, 5, 6}
    )


BALANCES_CSV_HEADERS = ["business", "period_start", "period_end", "section", "code", "item", "amount"]


def balances_csv_rows(report: Balances) -> List[List[str]]:
    def row(section: str, code: str, item: str, cents: Optional[int] = None) -> List[str]:
        amount = "" if cents is None else plain_cents(cents)
        return [report.business, report.period.start.isoformat(), report.period.end.isoformat(), section, code, item, amount]

    rows = []
    for account in report.accounts:
        name = f"{account.name} (owed)" if account.kind == "credit_card" else account.name
        rows.append(row("account start", "", name, account.printed(account.begin)))
        rows.append(row("account end", "", name, account.printed(account.end)))
    rows.append(row("money", "", "Money at the start", report.money_begin))
    if report.money_opened:
        rows.append(row("money", "", "Accounts opened in this period", report.money_opened))
    rows.append(row("money", "", "Net profit or (loss)", report.profit))
    rows.extend(row("money", entry.code, entry.name, entry.total) for entry in report.off_profit)
    if report.waiting_count:
        rows.append(row("money", "", "Waiting for review", report.waiting_net))
    rows.append(row("money", "", "Money at the end", report.money_end))
    if report.inventory_end is not None:
        rows.append(row("inventory", "", "Inventory count", report.inventory_end))
    rows.extend(row("note", "", note) for note in [*report.warnings, NOT_ADVICE])
    return rows


# Reconciliation

def reconcile_text(report: ReconcileReport) -> str:
    account = report.account
    recon = report.reconciliation
    owed = account["kind"] == "credit_card"
    balance_word = " (amount owed)" if owed else ""
    rows = [
        [f"Statement beginning balance{balance_word}", printed(account, report.statement_begin)],
        [f"Money in, {report.row_count} rows in period", format_cents(report.money_in)],
        ["Money out", format_cents(-report.money_out)],
        [f"Books say ending balance{balance_word}", printed(account, report.expected_end)],
        [f"Statement ending balance{balance_word}", printed(account, report.statement_end)],
    ]
    lines = [
        f"Reconciliation {recon['id']}: {account['name']}, {report.start} to {report.end} [{recon['status']}]",
        "",
        table(["", "Amount"], rows, {1}),
        "",
    ]
    if report.difference == 0:
        lines.append("Difference: 0.00")
    else:
        direction = "more money in (or less out)" if report.difference > 0 else "more money out (or less in)"
        lines.append(
            f"Difference: {format_cents(abs(report.difference))}. The statement shows {direction} than the books."
        )
    if report.begin_gap:
        lines.append(
            f"Beginning balance: statement {printed(account, report.statement_begin)}, "
            f"books {printed(account, report.book_begin)}, off by {format_cents(abs(report.begin_gap))}."
        )
    if report.prior_gap:
        lines.append(
            f"Beginning balance differs from the ending balance of reconciliation {report.prior_id} "
            f"by {format_cents(abs(report.prior_gap))}."
        )
    if report.hints:
        lines.append("")
        lines.append("Where to look:")
        lines.extend(f"  {hint}" for hint in report.hints)
    if report.compared_statement:
        lines.extend(_statement_lines(report))
    if report.rows_added_after_finish:
        lines.append("")
        lines.append("Rows added after this period was finished:")
        for row in report.rows_added_after_finish:
            lines.append(f"  {row['id']}  {row['posted_on']}  {format_cents(row['amount_cents'])}  {row['description']}")
    if report.notes:
        lines.append("")
        lines.extend(report.notes)
    lines.append("")
    if report.balanced:
        lines.append("RESULT: ties to the cent.")
        if recon["status"] == "open":
            lines.append(f"Lock it with: reconcile finish {recon['id']}")
    else:
        lines.append("RESULT: DOES NOT MATCH.")
    return "\n".join(lines)


def _statement_lines(report: ReconcileReport) -> List[str]:
    lines = ["", "Line by line against the statement file:"]
    clean = True
    if report.statement_only:
        clean = False
        lines.append("  On the statement, not in the books:")
        for line in report.statement_only:
            lines.append(f"    {line.posted_on}  {format_cents(line.amount_cents):>12}  {line.description}")
    if report.book_only:
        clean = False
        lines.append("  In the books, not on the statement:")
        for row in report.book_only:
            lines.append(f"    {row['posted_on']}  {format_cents(row['amount_cents']):>12}  {row['description']}  (transaction {row['id']})")
    if report.amount_differs:
        clean = False
        lines.append("  Same day and merchant, different amount:")
        for line, row in report.amount_differs:
            lines.append(
                f"    {line.posted_on}  statement {format_cents(line.amount_cents)}, "
                f"books {format_cents(row['amount_cents'])}  {line.description}  (transaction {row['id']})"
            )
    if report.date_differs:
        clean = False
        lines.append("  Same amount, different date (does not change the balance):")
        for line, row in report.date_differs:
            lines.append(
                f"    statement {line.posted_on}, books {row['posted_on']}  "
                f"{format_cents(line.amount_cents):>12}  {line.description}  (transaction {row['id']})"
            )
    if clean:
        lines.append("  Every line matches.")
    if report.statement_rows_outside:
        lines.append(f"  {report.statement_rows_outside} statement file row(s) were dated outside this period and ignored.")
    return lines
