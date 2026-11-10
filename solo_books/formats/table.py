"""Plain text for the terminal: aligned tables, one business after another, then a summary of all of them."""
from __future__ import annotations

from typing import List

from .. import render
from ..money import format_cents
from ..reports import NOT_ADVICE
from .base import Document, Format, Paragraph, Report, Table
from .layout import document


def _pnl(report: Report) -> str:
    parts = report.parts
    only = report.only
    chunks = [_tagged(only, render.pnl_text(pnl, report.show_date)) for pnl in parts]
    if len(parts) > 1:
        rows = [
            [
                pnl.business,
                format_cents(sum(pnl.total_income)),
                format_cents(sum(pnl.total_cogs)),
                format_cents(sum(pnl.total_expenses)),
                format_cents(sum(pnl.net)),
            ]
            for pnl in parts
        ]
        rows.append(
            [
                "All businesses",
                format_cents(sum(sum(p.total_income) for p in parts)),
                format_cents(sum(sum(p.total_cogs) for p in parts)),
                format_cents(sum(sum(p.total_expenses) for p in parts)),
                format_cents(sum(sum(p.net) for p in parts)),
            ]
        )
        chunks.append(
            f"All businesses, {report.period.label}" + (f". {only}" if only else "") + "\n"
            + render.table(["Business", "Income", "Cost of goods", "Expenses", "Net"], rows, {1, 2, 3, 4})
        )
    return "\n\n".join(chunks)


def _tagged(only: str, text: str) -> str:
    return f"{only}\n{text}" if only else text


def _balances(report: Report) -> str:
    chunks = [render.balances_text(part, report.show_date) for part in report.parts]
    if len(report.parts) > 1:
        chunks.append(render.balances_summary_text(report.parts))
    return "\n\n".join(chunks)


def _schedule_c(report: Report) -> str:
    return "\n\n".join(render.schedule_c_text(part, report.all_lines) for part in report.parts)


def _payees(report: Report) -> str:
    return "\n\n".join(_tagged(report.only, render.payees_text(part, report.limit)) for part in report.parts)


def _contractors(report: Report) -> str:
    return "\n\n".join(render.contractors_text(part) for part in report.parts)


def _budget(report: Report) -> str:
    return "\n\n".join(_tagged(report.only, render.budget_text(part, report.show_date)) for part in report.parts)


def _table_text(block: Table) -> str:
    return render.table(block.headers, block.rows, block.numeric)


def document_text(doc: Document) -> str:
    """Any laid-out report as terminal text: each section's heading, its lines, then its blocks."""
    chunks: List[str] = []
    for section in doc.sections:
        lines = [section.heading, *section.intro]
        for block in section.blocks:
            if isinstance(block, Paragraph):
                lines.append(f"WARNING: {block.text}" if block.warning else block.text)
            else:
                lines.extend(["", _table_text(block), ""])
        chunks.append("\n".join(lines).rstrip("\n"))
    chunks.append(NOT_ADVICE)
    return "\n\n".join(chunks)


# These reports keep the exact text they have always had.
_TEXT = {
    "pnl": _pnl, "balances": _balances, "schedule-c": _schedule_c, "payees": _payees, "contractors": _contractors,
    "budget": _budget,
}


class TableFormat(Format):
    name = "table"
    help = "aligned text for the terminal (the default)"
    byte_order_mark = True

    def render(self, report: Report) -> str:
        if report.kind in _TEXT:
            return _TEXT[report.kind](report)
        return document_text(document(report))


FORMAT = TableFormat()
