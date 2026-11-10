"""Each report laid out as a printable Document: headings, short paragraphs and tables of text.

HTML and Markdown render these, and so does the table format for the reports that have no older text layout.
Every figure here comes from the report object; nothing is added up again except the all-businesses summaries.
"""
from __future__ import annotations

from typing import Callable, Dict, List

from .. import render
from ..analysis import percent_change
from ..money import format_cents
from ..reports import Balances, Pnl, PnlRow
from .base import Document, Paragraph, Report, Section, Table

CARD_NOTE = "On a card, money in pays down what is owed and money out adds to it."


def _period_line(period) -> str:
    return f"Period: {period.label} ({period.start} to {period.end}), cash basis"


def _notes(lines: List[str]) -> List[Paragraph]:
    """Notes from the text reports. Those that open with WARNING: are marked as warnings instead."""
    result = []
    for line in lines:
        if line.startswith("WARNING: "):
            result.append(Paragraph(line[len("WARNING: "):], warning=True))
        else:
            result.append(Paragraph(line))
    return result


def _summary_section(period, headers: List[str], rows: List[List[str]]) -> Section:
    table = Table(["Business", *headers], numeric=set(range(1, len(headers) + 1)))
    for index, row in enumerate(rows):
        table.add(row, "total" if index == len(rows) - 1 else "")
    return Section(f"All businesses, {period.label}", blocks=[table])


# Profit and loss

def _pnl_table(labels: List[str], sections, totals, with_total: bool, extra=None, extra_headers=()) -> Table:
    """Shared by profit and loss and the trend: a label column, one column per bucket, and an optional total.

    extra, when given, adds columns worked out from each row's values, such as the trend's change.
    """
    headers = ["", *labels] + (["Total"] if with_total else []) + list(extra_headers)
    table = Table(headers, numeric=set(range(1, len(headers))))

    def cells(values: List[int]) -> List[str]:
        shown = [format_cents(v) for v in values] + ([format_cents(sum(values))] if with_total else [])
        return shown + (extra(values) if extra else [])

    for title, rows, negate, total_label, total_values in sections:
        if title:
            table.add([title] + [""] * (len(headers) - 1), "group")
        for entry in rows:
            values = [-v for v in entry.values] if negate else entry.values
            table.add([f"  {entry.code} {entry.name}", *cells(values)])
        if total_label:
            table.add([total_label, *cells(total_values)], "total")
    for label, values in totals:
        table.add([label, *cells(values)], "total")
    return table


def _pnl_sections(pnl: Pnl) -> list:
    sections = [
        ("INCOME", pnl.income, False, None, None),
        (None, pnl.returns, True, "Total income", pnl.total_income),
    ]
    if pnl.cogs:
        sections.append(("COST OF GOODS SOLD", pnl.cogs, False, "Gross profit", pnl.gross_profit))
    sections.append(("EXPENSES", pnl.expenses, False, "Total expenses", pnl.total_expenses))
    return sections


def _excluded_table(rows: List[PnlRow]) -> Table:
    table = Table(["Not income or expense (net money in, negative for money out)", "Amount"], numeric={1})
    for entry in rows:
        table.add([f"{entry.code} {entry.name}", format_cents(entry.total)])
    return table


def pnl_document(report: Report) -> Document:
    doc = Document("Profit and loss")
    for pnl in report.parts:
        section = Section(f"Profit and loss: {pnl.business}", [_period_line(pnl.period), *([report.only] if report.only else [])])
        sections = _pnl_sections(pnl)
        # Returns sit under income with no heading of their own, as the text report has them.
        section.blocks.append(
            _pnl_table([b.label for b in pnl.buckets], sections, [("NET PROFIT OR (LOSS)", pnl.net)], len(pnl.buckets) > 1)
        )
        off = pnl.off
        if off.uncategorized_count:
            section.blocks.append(Paragraph(
                f"{off.uncategorized_count} uncategorized row(s) are NOT in these figures "
                f"(in {format_cents(off.uncategorized_in)}, out {format_cents(off.uncategorized_out)}). Run 'review'.",
                warning=True,
            ))
        if off.excluded:
            section.blocks.append(_excluded_table(off.excluded))
        doc.sections.append(section)
    if len(report.parts) > 1:
        rows = [
            [p.business, *(format_cents(sum(v)) for v in (p.total_income, p.total_cogs, p.total_expenses, p.net))]
            for p in report.parts
        ]
        rows.append(["All businesses", *(
            format_cents(sum(sum(getattr(p, name)) for p in report.parts))
            for name in ("total_income", "total_cogs", "total_expenses", "net")
        )])
        doc.sections.append(_summary_section(report.period, ["Income", "Cost of goods", "Expenses", "Net"], rows))
    return doc


# Balances

def balances_document(report: Report) -> Document:
    doc = Document("Balances")
    for part in report.parts:
        section = Section(f"Balances: {part.business}", [_period_line(part.period)])
        accounts = Table(["Account", "Kind", "Start", "End"], numeric={2, 3})
        for a in part.accounts:
            accounts.add([a.name, a.kind, format_cents(a.printed(a.begin)), format_cents(a.printed(a.end))])
        section.blocks.append(accounts)
        if any(a.kind == "credit_card" for a in part.accounts):
            section.blocks.append(Paragraph("Card balances show what is owed."))
        moved = Table(["What moved the money", "Amount"], numeric={1})
        lines = render._balance_lines(part)
        for index, row in enumerate(lines):
            moved.add(row, "total" if index in (0, len(lines) - 1) else "")
        section.blocks.append(moved)
        if part.inventory_end is not None:
            section.blocks.append(Paragraph(
                f"Inventory count at {part.period.end}: {format_cents(part.inventory_end)} (from 'inventory set'). "
                f"Money plus inventory: {format_cents(part.money_end + part.inventory_end)}."
            ))
        section.blocks.extend(Paragraph(w, warning=True) for w in part.warnings)
        doc.sections.append(section)
    if len(report.parts) > 1:
        def row(name: str, parts: List[Balances]) -> List[str]:
            return [name] + [
                format_cents(sum(value(p) for p in parts))
                for value in (
                    lambda p: p.money_begin + p.money_opened,
                    lambda p: p.profit,
                    lambda p: p.owner_net,
                    lambda p: p.transfers_net,
                    lambda p: p.waiting_net,
                    lambda p: p.money_end,
                )
            ]

        rows = [row(p.business, [p]) for p in report.parts] + [row("All businesses", report.parts)]
        doc.sections.append(
            _summary_section(report.period, ["Start", "Profit", "Owner money", "Transfers", "Waiting", "End"], rows)
        )
    return doc


# Schedule C

def schedule_c_document(report: Report) -> Document:
    doc = Document("Schedule C rollup")
    for part in report.parts:
        section = Section(
            f"Schedule C rollup: {part.business}, tax year {part.tax_year}",
            [f"Lines from: {part.form.stamp()}", "Hand this to your preparer with the transaction detail."],
        )
        table = Table(["Line", "Description", "Amount"], numeric={2})
        current_part = None
        for entry in part.lines:
            if not (report.all_lines or entry.computed or entry.cents != 0):
                continue
            if entry.entry["part"] != current_part:
                current_part = entry.entry["part"]
                table.add(["", render._PART_TITLES.get(current_part, f"Part {current_part}"), ""], "group")
            table.add([entry.line, entry.label, format_cents(entry.cents)], "total" if entry.computed else "")
            if entry.categories and (len(entry.categories) > 1 or entry.line == "48"):
                for category in entry.categories:
                    table.add(["", f"  {category.code} {category.name}", format_cents(category.total)])
        section.blocks.append(table)
        section.blocks.extend(_notes(render._schedule_notes(part)))
        doc.sections.append(section)
    return doc


# Cash flow

def _flow_cells(line, index: int) -> List[str]:
    if index < 0:
        return [format_cents(line.total_in), format_cents(line.total_out), format_cents(line.total_net)]
    return [format_cents(line.money_in[index]), format_cents(line.money_out[index]), format_cents(line.net[index])]


def cashflow_document(report: Report) -> Document:
    doc = Document("Cash flow")
    for part in report.parts:
        section = Section(f"Cash flow: {part.business}", [_period_line(part.period)])
        table = Table(["Period", "", "Money in", "Money out", "Net"], numeric={2, 3, 4})
        indexes = list(range(len(part.buckets))) + ([-1] if len(part.buckets) > 1 else [])
        total = part.total
        for index in indexes:
            label = part.buckets[index].label if index >= 0 else "Whole period"
            first = True
            for account in part.accounts:
                table.add([label if first else "", account.label, *_flow_cells(account, index)])
                first = False
            table.add([label if first else "", total.label, *_flow_cells(total, index)], "total")
            for purpose in part.purposes + ([part.waiting] if part.waiting else []):
                table.add(["", f"  {purpose.label}", *_flow_cells(purpose, index)])
        section.blocks.append(table)
        if any(account.account_kind == "credit_card" for account in part.accounts):
            section.blocks.append(Paragraph(CARD_NOTE))
        section.blocks.extend(Paragraph(w, warning=True) for w in part.warnings)
        doc.sections.append(section)
    if len(report.parts) > 1:
        rows = [[p.business, *_flow_cells(p.total, -1)] for p in report.parts]
        rows.append(["All businesses"] + [
            format_cents(sum(value(p.total) for p in report.parts))
            for value in (lambda t: t.total_in, lambda t: t.total_out, lambda t: t.total_net)
        ])
        doc.sections.append(_summary_section(report.period, ["Money in", "Money out", "Net"], rows))
    return doc


# Trend

def _change(values: List[int]) -> List[str]:
    first, last = values[0], values[-1]
    return [format_cents(last - first), percent_change(first, last) or ""]


def trend_document(report: Report) -> Document:
    doc = Document("Trend")
    for part in report.parts:
        pnl = part.pnl
        years = [str(year) for year in part.years]
        section = Section(f"Trend: {part.business}", [f"Years: {years[0]} to {years[-1]}, cash basis"])
        # Net profit comes before the rows that are not profit, so it goes in as a section of its own.
        sections = _pnl_sections(pnl) + [(None, [], False, "NET PROFIT OR (LOSS)", pnl.net)]
        if pnl.off.excluded:
            sections.append(("NOT INCOME OR EXPENSE", pnl.off.excluded, False, None, None))
        table = _pnl_table(years, sections, [], False, _change, ("Change", "Change %"))
        section.blocks.append(table)
        if any(part.waiting):
            counts = ", ".join(f"{year}: {count}" for year, count in zip(part.years, part.waiting) if count)
            section.blocks.append(Paragraph(f"Rows waiting for review are NOT in these figures ({counts}). Run 'review'.", warning=True))
        section.blocks.append(Paragraph("Change is the last year less the first."))
        section.blocks.append(Paragraph("Change % compares that with the first year, and is blank when the first year is zero."))
        doc.sections.append(section)
    return doc


# Categories

def categories_document(report: Report) -> Document:
    doc = Document("Categories")
    for part in report.parts:
        section = Section(f"Categories: {part.business}", [_period_line(part.period)])
        table = Table(["Code", "Category", "Rows", "Total", "Average", "Largest", "Row", "Largest row"], numeric={2, 3, 4, 5, 6})
        stats = part.categories + ([part.waiting] if part.waiting else [])
        for stat in stats:
            table.add(
                [
                    stat.code,
                    stat.name,
                    str(stat.rows),
                    format_cents(stat.total),
                    format_cents(stat.average),
                    format_cents(stat.largest_amount),
                    str(stat.largest_id),
                    f"{stat.largest_on} {stat.largest_description}",
                ],
                "total" if stat is part.waiting else "",
            )
        if not stats:
            section.blocks.append(Paragraph("No rows in this period."))
        else:
            section.blocks.append(table)
        if part.waiting:
            section.blocks.append(Paragraph(
                f"{part.waiting.rows} row(s) are waiting for review and are on their own line. Run 'review'.", warning=True
            ))
        section.blocks.append(Paragraph("Amounts read as profit and loss reads them, so an expense is positive."))
        section.blocks.append(Paragraph("Owner money, transfers and the review queue keep the bank's sign: positive is money in."))
        section.blocks.append(Paragraph("Largest is the one row that moved the most money. 'txn show' with its row number shows it."))
        doc.sections.append(section)
    return doc


LAYOUTS: Dict[str, Callable[[Report], Document]] = {
    "pnl": pnl_document,
    "balances": balances_document,
    "schedule-c": schedule_c_document,
    "cashflow": cashflow_document,
    "trend": trend_document,
    "categories": categories_document,
}


def document(report: Report) -> Document:
    return LAYOUTS[report.kind](report)
