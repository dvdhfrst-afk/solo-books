"""Budgets: a planned amount per category per year or per month, and how the books compare with it.

Amounts are entered the way reports read them: expected income as a positive figure, expected spending as a positive
figure. A whole-year amount is spread over the months in whole cents, so twelve months always add up to it exactly.
"""
from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from . import history
from .books import list_categories
from .db import transaction
from .errors import BooksError
from .periods import Period, check_year
from .reports import OffReport, profit_and_loss

BUDGET_KINDS = ("income", "returns", "cogs", "expense")
YEARLY = 0


def month_share(yearly_cents: int, month: int) -> int:
    """One month's part of a year's amount. The twelve parts add up to the year to the cent."""
    return yearly_cents * month // 12 - yearly_cents * (month - 1) // 12


def _check_category(business: sqlite3.Row, category: sqlite3.Row) -> None:
    if category["business_id"] != business["id"]:
        raise BooksError(f"category {category['code']} {category['name']} belongs to another business")
    if category["kind"] not in BUDGET_KINDS:
        raise BooksError(
            f"{category['code']} {category['name']} is a {category['kind']} category. Only income and expense "
            "categories take a budget, since the others never reach profit and loss."
        )


def _amounts(conn: sqlite3.Connection, category_id: int, year: int) -> Dict[int, int]:
    return {
        row["month"]: row["amount_cents"]
        for row in conn.execute(
            "SELECT month, amount_cents FROM budgets WHERE category_id = ? AND year = ? ORDER BY month", (category_id, year)
        )
    }


def _monthly(amounts: Dict[int, int]) -> Dict[int, int]:
    """The twelve months of a year's budget, whichever way it was entered."""
    if YEARLY in amounts:
        return {month: month_share(amounts[YEARLY], month) for month in range(1, 13)}
    return {month: amounts.get(month, 0) for month in range(1, 13)}


def _label(category: sqlite3.Row) -> str:
    return f"{category['code']} {category['name']}"


def _write(conn: sqlite3.Connection, business_id: int, category_id: int, year: int, amounts: Dict[int, int]) -> None:
    conn.execute("DELETE FROM budgets WHERE category_id = ? AND year = ?", (category_id, year))
    conn.executemany(
        "INSERT INTO budgets (business_id, category_id, year, month, amount_cents) VALUES (?, ?, ?, ?, ?)",
        [(business_id, category_id, year, month, cents) for month, cents in sorted(amounts.items())],
    )


def set_budget(
    conn: sqlite3.Connection,
    business: sqlite3.Row,
    category: sqlite3.Row,
    year: int,
    monthly: Optional[int] = None,
    yearly: Optional[int] = None,
    month: Optional[int] = None,
) -> Dict:
    """Set a year's amount, every month's amount, or one month's amount. The others become months of their own."""
    _check_category(business, category)
    check_year(year)
    if (monthly is None) == (yearly is None):
        raise BooksError("give a monthly amount or a yearly amount")
    for cents in (monthly, yearly):
        if cents is not None and cents < 0:
            raise BooksError("a budget is entered as a positive amount, for income and for spending alike")
    if month is not None and monthly is None:
        raise BooksError("one month takes a monthly amount: --month with --monthly")
    if month is not None and not 1 <= month <= 12:
        raise BooksError(f"month {month} is not 1 to 12")
    before = _amounts(conn, category["id"], year)
    if yearly is not None:
        after = {YEARLY: yearly}
    elif month is None:
        after = {each: monthly for each in range(1, 13)}
    else:
        after = _monthly(before) if before else {each: 0 for each in range(1, 13)}
        after[month] = monthly  # type: ignore[assignment]
    with transaction(conn):
        _write(conn, business["id"], category["id"], year, after)
        history.record(
            conn, "budget-set", f"Set the {year} budget for {_label(category)}.", business["id"],
            {
                "category_id": category["id"], "category": _label(category), "year": year,
                "before": {str(key): value for key, value in before.items()},
                "after": {str(key): value for key, value in after.items()},
            },
        )
    return {"spread": YEARLY in before and month is not None, "year_total": sum(_monthly(after).values())}


def delete_budget(conn: sqlite3.Connection, business: sqlite3.Row, category: sqlite3.Row, year: int) -> int:
    _check_category(business, category)
    before = _amounts(conn, category["id"], year)
    if not before:
        raise BooksError(f"{_label(category)} has no budget for {year}")
    with transaction(conn):
        conn.execute("DELETE FROM budgets WHERE category_id = ? AND year = ?", (category["id"], year))
        history.record(
            conn, "budget-delete", f"Deleted the {year} budget for {_label(category)}.", business["id"],
            {"category_id": category["id"], "category": _label(category), "year": year,
             "before": {str(key): value for key, value in before.items()}},
        )
    return sum(_monthly(before).values())


def copy_budgets(conn: sqlite3.Connection, business: sqlite3.Row, from_year: int, to_year: int, replace: bool = False) -> int:
    """Start a year from last year's plan. A year that already has a plan is only overwritten when asked."""
    check_year(from_year)
    check_year(to_year)
    if from_year == to_year:
        raise BooksError("copy from one year to a different year")
    rows = conn.execute(
        "SELECT category_id, month, amount_cents FROM budgets WHERE business_id = ? AND year = ?", (business["id"], from_year)
    ).fetchall()
    if not rows:
        raise BooksError(f"{business['name']} has no budget for {from_year} to copy")
    existing = conn.execute(
        "SELECT COUNT(DISTINCT category_id) FROM budgets WHERE business_id = ? AND year = ?", (business["id"], to_year)
    ).fetchone()[0]
    if existing and not replace:
        raise BooksError(
            f"{business['name']} already has a {to_year} budget for {existing} category(s). "
            "Pass --replace to overwrite that year with the copy."
        )
    with transaction(conn):
        conn.execute("DELETE FROM budgets WHERE business_id = ? AND year = ?", (business["id"], to_year))
        conn.executemany(
            "INSERT INTO budgets (business_id, category_id, year, month, amount_cents) VALUES (?, ?, ?, ?, ?)",
            [(business["id"], row["category_id"], to_year, row["month"], row["amount_cents"]) for row in rows],
        )
        categories = len({row["category_id"] for row in rows})
        history.record(
            conn, "budget-copy", f"Copied the {from_year} budget ({categories} category(s)) to {to_year}.", business["id"],
            {"from": from_year, "to": to_year, "categories": categories, "replaced": existing},
        )
    return categories


@dataclass
class BudgetYear:
    business: str
    code: str
    name: str
    kind: str
    year: int
    yearly: bool
    months: Dict[int, int]

    @property
    def total(self) -> int:
        return sum(self.months.values())

    @property
    def same_every_month(self) -> Optional[int]:
        values = set(self.months.values())
        return values.pop() if len(values) == 1 and not self.yearly else None


def list_budgets(conn: sqlite3.Connection, business_id: Optional[int] = None, year: Optional[int] = None) -> List[BudgetYear]:
    where, params = [], []  # type: ignore[var-annotated]
    if business_id is not None:
        where.append("g.business_id = ?")
        params.append(business_id)
    if year is not None:
        where.append("g.year = ?")
        params.append(year)
    rows = conn.execute(
        "SELECT g.*, b.name AS business_name, c.code, c.name, c.kind FROM budgets g"
        " JOIN businesses b ON b.id = g.business_id JOIN categories c ON c.id = g.category_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY b.name, g.year, c.code, g.month",
        params,
    ).fetchall()
    found: Dict[Tuple[int, int], Tuple[sqlite3.Row, Dict[int, int]]] = {}
    for row in rows:
        found.setdefault((row["category_id"], row["year"]), (row, {}))[1][row["month"]] = row["amount_cents"]
    return [
        BudgetYear(row["business_name"], row["code"], row["name"], row["kind"], row["year"], YEARLY in amounts, _monthly(amounts))
        for row, amounts in found.values()
    ]


def _whole_months(period: Period) -> List[Tuple[int, int]]:
    last_day = calendar.monthrange(period.end.year, period.end.month)[1]
    if period.start.day != 1 or period.end != date(period.end.year, period.end.month, last_day):
        raise BooksError(
            f"budgets are kept by the month, so the period must start on the first of a month and end on the last day "
            f"of one. {period.label} does not."
        )
    months = []
    year, month = period.start.year, period.start.month
    while (year, month) <= (period.end.year, period.end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def budget_for_period(conn: sqlite3.Connection, business_id: int, period: Period) -> Dict[int, int]:
    """What each category was budgeted over a run of whole months."""
    months = _whole_months(period)
    years = sorted({year for year, _ in months})
    plans: Dict[Tuple[int, int], Dict[int, int]] = {}
    for row in conn.execute(
        f"SELECT category_id, year, month, amount_cents FROM budgets WHERE business_id = ? AND year IN ({', '.join('?' * len(years))})",
        (business_id, *years),
    ):
        plans.setdefault((row["category_id"], row["year"]), {})[row["month"]] = row["amount_cents"]
    totals: Dict[int, int] = {}
    for (category_id, year), amounts in plans.items():
        spread = _monthly(amounts)
        totals[category_id] = totals.get(category_id, 0) + sum(spread[month] for each_year, month in months if each_year == year)
    return totals


@dataclass
class BudgetLine:
    """Budget and actual for one category, signed the way profit and loss shows them: returns reduce income."""

    code: str
    name: str
    kind: str
    budget: int
    actual: int

    @property
    def difference(self) -> int:
        return self.actual - self.budget


@dataclass
class BudgetReport:
    business: str
    period: Period
    income: List[BudgetLine]
    cogs: List[BudgetLine]
    expenses: List[BudgetLine]
    off: OffReport
    warnings: List[str] = field(default_factory=list)

    def sections(self) -> List[Tuple[str, str, List[BudgetLine], str, Tuple[int, int]]]:
        """Each section's name, heading, lines, total label and total, in the order profit and loss shows them."""
        found = [("income", "INCOME", self.income, "Total income", self.total_income)]
        if self.cogs:
            found.append(("cost of goods sold", "COST OF GOODS SOLD", self.cogs, "Total cost of goods sold", self.total_cogs))
        found.append(("expense", "EXPENSES", self.expenses, "Total expenses", self.total_expenses))
        return found

    @staticmethod
    def _sum(lines: List[BudgetLine]) -> Tuple[int, int]:
        return sum(line.budget for line in lines), sum(line.actual for line in lines)

    @property
    def total_income(self) -> Tuple[int, int]:
        return self._sum(self.income)

    @property
    def total_cogs(self) -> Tuple[int, int]:
        return self._sum(self.cogs)

    @property
    def total_expenses(self) -> Tuple[int, int]:
        return self._sum(self.expenses)

    @property
    def net(self) -> Tuple[int, int]:
        income, cogs, expenses = self.total_income, self.total_cogs, self.total_expenses
        return income[0] - cogs[0] - expenses[0], income[1] - cogs[1] - expenses[1]


def budget_report(
    conn: sqlite3.Connection, business: sqlite3.Row, period: Period, tag_id: Optional[int] = None
) -> BudgetReport:
    """Budget against the books for a year, quarter or month. Actual figures are the profit and loss figures.

    With a tag, the actual figures are that tag's rows, set against the whole budget.
    """
    planned = budget_for_period(conn, business["id"], period)
    pnl = profit_and_loss(conn, business, period, tag_id=tag_id)
    actual = {row.code: row.total for row in pnl.income + pnl.returns + pnl.cogs + pnl.expenses}
    lines: Dict[str, List[BudgetLine]] = {"income": [], "cogs": [], "expense": []}
    for category in list_categories(conn, business["id"], include_retired=True):
        if category["kind"] not in BUDGET_KINDS:
            continue
        budget, spent = planned.get(category["id"]), actual.get(category["code"])
        if budget is None and not spent:
            continue
        sign = -1 if category["kind"] == "returns" else 1
        section = "income" if category["kind"] in ("income", "returns") else category["kind"]
        lines[section].append(BudgetLine(category["code"], category["name"], category["kind"], sign * (budget or 0), sign * (spent or 0)))
    warnings = []
    unplanned = [line for section in lines.values() for line in section if line.budget == 0 and line.actual]
    if unplanned:
        warnings.append(f"{len(unplanned)} category(s) have money but no budget for this period.")
    return BudgetReport(business["name"], period, lines["income"], lines["cogs"], lines["expense"], pnl.off, warnings)


def percent_used(actual: int, budget: int) -> Optional[str]:
    """Actual as a percentage of budget, to a tenth, worked out in whole numbers."""
    if budget == 0:
        return None
    tenths = (abs(actual) * 2000 + abs(budget)) // (2 * abs(budget))
    negative = (actual < 0) != (budget < 0) and actual != 0
    return f"{'-' if negative else ''}{tenths // 10}.{tenths % 10}%"


def problems(conn: sqlite3.Connection) -> List[str]:
    """What 'check' reports about budgets: one for another business's category, or for a category that takes none."""
    found: List[str] = []
    crossed = conn.execute(
        "SELECT COUNT(*) FROM budgets g JOIN categories c ON c.id = g.category_id WHERE c.business_id != g.business_id"
    ).fetchone()[0]
    if crossed:
        found.append(f"{crossed} budget amount(s) belong to another business's category.")
    off = conn.execute(
        "SELECT COUNT(*) FROM budgets g JOIN categories c ON c.id = g.category_id"
        f" WHERE c.kind NOT IN ({', '.join('?' * len(BUDGET_KINDS))})",
        BUDGET_KINDS,
    ).fetchone()[0]
    if off:
        found.append(f"{off} budget amount(s) are for transfer or owner categories, which never reach profit and loss.")
    mixed = conn.execute(
        "SELECT COUNT(*) FROM (SELECT category_id, year FROM budgets GROUP BY category_id, year"
        " HAVING SUM(month = 0) > 0 AND COUNT(*) > 1)"
    ).fetchone()[0]
    if mixed:
        found.append(f"{mixed} budget(s) have both a yearly amount and monthly amounts for the same year.")
    return found
