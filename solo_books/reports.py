"""Profit and loss and the Schedule C rollup, worked out from the books.

Both start from the same per-category totals. Figures are positive in the way the report reads:
income is positive when money came in, an expense is positive when money went out, and a refund
reduces the category it was booked to.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from .books import get_inventory
from .errors import BooksError
from .money import format_cents
from .periods import Period, parse_iso_date, split_period, year_period
from .schedule_c import Form, line_label, load_form

NOT_ADVICE = "Bookkeeping summary, not tax advice."

_SECTION_SIGN = {"income": 1, "returns": -1, "cogs": -1, "expense": -1}


def figure(kind: str, amount_cents: int) -> int:
    """A category's figure as a report shows it, from the signed sum of its rows."""
    return _SECTION_SIGN[kind] * amount_cents


@dataclass
class PnlRow:
    code: str
    name: str
    kind: str
    values: List[int]

    @property
    def total(self) -> int:
        return sum(self.values)


@dataclass
class OffReport:
    """Rows deliberately kept out of profit: transfers, owner money, and anything unreviewed."""

    uncategorized_count: int = 0
    uncategorized_in: int = 0
    uncategorized_out: int = 0
    excluded: List[PnlRow] = field(default_factory=list)


@dataclass
class Pnl:
    business: str
    business_id: int
    period: Period
    buckets: List[Period]
    income: List[PnlRow]
    returns: List[PnlRow]
    cogs: List[PnlRow]
    expenses: List[PnlRow]
    off: OffReport

    def _sum(self, rows: List[PnlRow]) -> List[int]:
        return [sum(row.values[i] for row in rows) for i in range(len(self.buckets))]

    @property
    def total_income(self) -> List[int]:
        income, returns = self._sum(self.income), self._sum(self.returns)
        return [a - b for a, b in zip(income, returns)]

    @property
    def total_cogs(self) -> List[int]:
        return self._sum(self.cogs)

    @property
    def gross_profit(self) -> List[int]:
        return [a - b for a, b in zip(self.total_income, self.total_cogs)]

    @property
    def total_expenses(self) -> List[int]:
        return self._sum(self.expenses)

    @property
    def net(self) -> List[int]:
        return [a - b for a, b in zip(self.gross_profit, self.total_expenses)]


def _bucket_index(buckets: List[Period], day: date) -> int:
    for index, bucket in enumerate(buckets):
        if bucket.contains(day):
            return index
    raise AssertionError(f"{day} is outside the report period")


def profit_and_loss(conn: sqlite3.Connection, business: sqlite3.Row, period: Period, by: str = "total") -> Pnl:
    buckets = split_period(period, by)
    rows = conn.execute(
        "SELECT t.posted_on, t.amount_cents, c.id AS category_id, c.code, c.name, c.kind"
        " FROM transactions t JOIN accounts a ON a.id = t.account_id"
        " LEFT JOIN categories c ON c.id = t.category_id"
        " WHERE a.business_id = ? AND t.posted_on BETWEEN ? AND ?",
        (business["id"], period.start.isoformat(), period.end.isoformat()),
    ).fetchall()

    off = OffReport()
    by_category: Dict[int, PnlRow] = {}
    for row in rows:
        if row["category_id"] is None:
            off.uncategorized_count += 1
            if row["amount_cents"] > 0:
                off.uncategorized_in += row["amount_cents"]
            else:
                off.uncategorized_out -= row["amount_cents"]
            continue
        entry = by_category.get(row["category_id"])
        if entry is None:
            entry = PnlRow(row["code"], row["name"], row["kind"], [0] * len(buckets))
            by_category[row["category_id"]] = entry
        index = _bucket_index(buckets, parse_iso_date(row["posted_on"]))
        if row["kind"] in _SECTION_SIGN:
            entry.values[index] += figure(row["kind"], row["amount_cents"])
        else:
            entry.values[index] += row["amount_cents"]

    ordered = sorted(by_category.values(), key=lambda entry: entry.code)
    off.excluded = [entry for entry in ordered if entry.kind in ("transfer", "equity")]
    return Pnl(
        business=business["name"],
        business_id=business["id"],
        period=period,
        buckets=buckets,
        income=[entry for entry in ordered if entry.kind == "income"],
        returns=[entry for entry in ordered if entry.kind == "returns"],
        cogs=[entry for entry in ordered if entry.kind == "cogs"],
        expenses=[entry for entry in ordered if entry.kind == "expense"],
        off=off,
    )


@dataclass
class ScheduleLine:
    entry: dict
    cents: int
    categories: List[PnlRow] = field(default_factory=list)

    @property
    def line(self) -> str:
        return self.entry["line"]

    @property
    def label(self) -> str:
        return line_label(self.entry)

    @property
    def computed(self) -> bool:
        return "compute" in self.entry


@dataclass
class ScheduleC:
    business: str
    tax_year: int
    form: Form
    lines: List[ScheduleLine]
    pnl_net: int
    off_form: List[PnlRow]
    off_form_net: int
    inventory_change: int
    inventory_entered: bool
    uncategorized_count: int
    uncategorized_in: int
    uncategorized_out: int
    warnings: List[str] = field(default_factory=list)

    def amount(self, line: str) -> int:
        for entry in self.lines:
            if entry.line == line:
                return entry.cents
        raise BooksError(f"no line {line} on the {self.form.tax_year} form")

    @property
    def net_line(self) -> ScheduleLine:
        return self.lines[[l.line for l in self.lines].index("31")]

    @property
    def ties(self) -> bool:
        """Line 31 equals profit, adjusted for inventory and for anything left off the form."""
        return self.net_line.cents == self.pnl_net + self.inventory_change - self.off_form_net


def schedule_c(
    conn: sqlite3.Connection, business: sqlite3.Row, tax_year: int, lines_year: Optional[int] = None
) -> ScheduleC:
    """Roll one business's calendar year up to the form's lines."""
    form = load_form(lines_year if lines_year is not None else tax_year)
    period = year_period(tax_year)
    pnl = profit_and_loss(conn, business, period)
    code_to_key = {
        row["code"]: row["schedule_c_key"]
        for row in conn.execute("SELECT code, schedule_c_key FROM categories WHERE business_id = ?", (business["id"],))
    }

    amounts: Dict[str, int] = {}
    details: Dict[str, List[PnlRow]] = {}
    off_form: List[PnlRow] = []
    off_form_net = 0
    warnings: List[str] = []
    if lines_year is not None and lines_year != tax_year:
        warnings.append(f"Books are for {tax_year} but the line names and numbers come from the {lines_year} form.")

    def book(rows: List[PnlRow], sign: int) -> None:
        nonlocal off_form_net
        for row in rows:
            key = code_to_key.get(row.code)
            entry = form.find_by_key(key) if key else None
            if entry is None:
                if row.total != 0:
                    off_form.append(row)
                    off_form_net += sign * row.total
                    warnings.append(
                        f"Category {row.code} {row.name} ({format_cents(row.total)}) has no line on the "
                        f"{form.tax_year} form, so it is left off. Point it at one with 'chart map'."
                    )
                continue
            amounts[entry["line"]] = amounts.get(entry["line"], 0) + row.total
            details.setdefault(entry["line"], []).append(row)

    book(pnl.income, 1)
    book(pnl.returns, -1)
    book(pnl.cogs, -1)
    book(pnl.expenses, -1)

    inventory = get_inventory(conn, business["id"], tax_year)
    beginning = inventory["beginning_cents"] if inventory else 0
    ending = inventory["ending_cents"] if inventory else 0
    begin_entry = form.find_by_key("inventory_beginning")
    end_entry = form.find_by_key("inventory_ending")
    if begin_entry is not None:
        amounts[begin_entry["line"]] = beginning
    if end_entry is not None:
        amounts[end_entry["line"]] = ending
    if inventory is None and pnl.cogs:
        warnings.append(
            "This business has cost of goods sold but no inventory counts for the year; both counts are taken as zero. "
            "Enter them with 'inventory set'."
        )

    by_line = {entry["line"]: entry for entry in form.lines}
    solved: Dict[str, int] = {}

    def solve(line: str) -> int:
        if line in solved:
            return solved[line]
        entry = by_line[line]
        if "compute" in entry:
            value = 0
            for term in entry["compute"]:
                part = solve(term[1:])
                value += part if term[0] == "+" else -part
        else:
            value = amounts.get(line, 0)
        solved[line] = value
        return value

    lines = [
        ScheduleLine(entry, solve(entry["line"]), sorted(details.get(entry["line"], []), key=lambda r: r.code))
        for entry in form.lines
    ]
    result = ScheduleC(
        business=business["name"],
        tax_year=tax_year,
        form=form,
        lines=lines,
        pnl_net=pnl.net[0],
        off_form=off_form,
        off_form_net=off_form_net,
        inventory_change=ending - beginning,
        inventory_entered=inventory is not None,
        uncategorized_count=pnl.off.uncategorized_count,
        uncategorized_in=pnl.off.uncategorized_in,
        uncategorized_out=pnl.off.uncategorized_out,
        warnings=warnings,
    )
    if not result.ties:
        raise AssertionError(
            f"Schedule C line 31 ({result.net_line.cents}) does not tie to profit and loss ({result.pnl_net})"
        )
    return result
