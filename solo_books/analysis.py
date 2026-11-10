"""Cash flow, year against year, and what each category holds, worked out from the books.

Like profit and loss, these read bank rows on the date in the export. Rows still in the review queue are kept on
their own line, never folded into a category.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from .books import list_accounts
from .errors import BooksError
from .periods import Period, check_year, parse_iso_date, split_period
from .reports import Pnl, figure, profit_and_loss

# Longest stretch of years one trend report lays side by side.
MAX_TREND_YEARS = 20

# What moved the money, in the order a cash flow report lists it. Rows with no category are the last group.
PURPOSES = (
    ("business", "Income and costs", ("income", "returns", "cogs", "expense")),
    ("owner", "Owner money", ("equity",)),
    ("transfer", "Transfers", ("transfer",)),
)
WAITING_LABEL = "Waiting for review"

# The order categories are listed in, by kind.
KIND_ORDER = ("income", "returns", "cogs", "expense", "equity", "transfer")


def divide_cents(total: int, count: int) -> int:
    """An average to the nearest cent, half a cent rounding away from zero. Whole numbers only."""
    if count <= 0:
        raise ValueError("count must be positive")
    quotient, remainder = divmod(abs(total), count)
    if remainder * 2 >= count:
        quotient += 1
    return -quotient if total < 0 else quotient


def percent_change(first: int, last: int) -> Optional[str]:
    """How far last moved from first, as a percentage with one decimal, or None when first is zero."""
    if first == 0:
        return None
    tenths = divide_cents((last - first) * 1000, abs(first))
    sign = "-" if tenths < 0 else "+" if tenths > 0 else ""
    return f"{sign}{abs(tenths) // 10}.{abs(tenths) % 10}"


# Cash flow

@dataclass
class FlowLine:
    """Money in and money out per bucket. Out is positive: it is how much left."""

    label: str
    money_in: List[int]
    money_out: List[int]
    rows: int = 0
    account_kind: Optional[str] = None

    @property
    def net(self) -> List[int]:
        return [a - b for a, b in zip(self.money_in, self.money_out)]

    @property
    def total_in(self) -> int:
        return sum(self.money_in)

    @property
    def total_out(self) -> int:
        return sum(self.money_out)

    @property
    def total_net(self) -> int:
        return self.total_in - self.total_out

    def add(self, index: int, amount_cents: int) -> None:
        self.rows += 1
        if amount_cents > 0:
            self.money_in[index] += amount_cents
        else:
            self.money_out[index] -= amount_cents


def _empty_line(label: str, size: int, account_kind: Optional[str] = None) -> FlowLine:
    return FlowLine(label, [0] * size, [0] * size, 0, account_kind)


@dataclass
class Cashflow:
    business: str
    period: Period
    buckets: List[Period]
    accounts: List[FlowLine]
    purposes: List[FlowLine]
    waiting: Optional[FlowLine]
    warnings: List[str] = field(default_factory=list)

    @property
    def total(self) -> FlowLine:
        size = len(self.buckets)
        line = _empty_line("All accounts", size)
        for account in self.accounts:
            line.rows += account.rows
            for index in range(size):
                line.money_in[index] += account.money_in[index]
                line.money_out[index] += account.money_out[index]
        return line

    @property
    def ties(self) -> bool:
        """The accounts and what moved their money are two views of the same rows."""
        parts = self.purposes + ([self.waiting] if self.waiting else [])
        total = self.total
        return (
            [sum(p.money_in[i] for p in parts) for i in range(len(self.buckets))] == total.money_in
            and [sum(p.money_out[i] for p in parts) for i in range(len(self.buckets))] == total.money_out
        )


def _bucket_of(buckets: List[Period], day: date) -> int:
    for index, bucket in enumerate(buckets):
        if bucket.contains(day):
            return index
    raise AssertionError(f"{day} is outside the report period")


def cashflow(conn: sqlite3.Connection, business: sqlite3.Row, period: Period, by: str = "total") -> Cashflow:
    """Money in and out of each account, and what moved it, from the bank rows alone."""
    buckets = split_period(period, by)
    size = len(buckets)
    accounts = {account["id"]: _empty_line(account["name"], size, account["kind"]) for account in list_accounts(conn, business["id"])}
    purposes = {key: _empty_line(label, size) for key, label, _ in PURPOSES}
    purpose_of_kind = {kind: key for key, _, kinds in PURPOSES for kind in kinds}
    waiting = _empty_line(WAITING_LABEL, size)
    rows = conn.execute(
        "SELECT t.account_id, t.posted_on, t.amount_cents, c.kind FROM transactions t"
        " JOIN accounts a ON a.id = t.account_id LEFT JOIN categories c ON c.id = t.category_id"
        " WHERE a.business_id = ? AND t.posted_on BETWEEN ? AND ?",
        (business["id"], period.start.isoformat(), period.end.isoformat()),
    ).fetchall()
    for row in rows:
        index = _bucket_of(buckets, parse_iso_date(row["posted_on"]))
        accounts[row["account_id"]].add(index, row["amount_cents"])
        target = waiting if row["kind"] is None else purposes[purpose_of_kind[row["kind"]]]
        target.add(index, row["amount_cents"])
    warnings: List[str] = []
    if waiting.rows:
        warnings.append(f"{waiting.rows} row(s) are waiting for review. Their money is shown on its own line. Run 'review'.")
    if purposes["transfer"].total_net:
        warnings.append(
            "Transfers do not net to zero. Each move should show on both accounts, so one side is missing, "
            "is booked to something else, or is in another business's account."
        )
    result = Cashflow(
        business["name"],
        period,
        buckets,
        list(accounts.values()),
        [purposes[key] for key, _, _ in PURPOSES],
        waiting if waiting.rows else None,
        warnings,
    )
    if not result.ties:
        raise AssertionError(f"cash flow for {business['name']} does not add up over {period.label}")
    return result


# Trend

@dataclass
class Trend:
    """Each category's total per calendar year, from profit and loss cut by year."""

    business: str
    years: List[int]
    pnl: Pnl
    waiting: List[int]

    @staticmethod
    def change(values: List[int]) -> int:
        """The last year less the first."""
        return values[-1] - values[0]


_YEARS = re.compile(r"^(\d{4})(?:\.\.(\d{4}))?$")


def parse_years(text: str) -> List[int]:
    """Read 2025 or 2023..2025."""
    match = _YEARS.match(text.strip())
    if not match:
        raise BooksError(f"not a run of years: {text!r} (use 2025 or 2023..2025)")
    first = check_year(int(match.group(1)))
    last = check_year(int(match.group(2) or match.group(1)))
    if last < first:
        raise BooksError(f"the years run backward: {text!r}")
    if last - first + 1 > MAX_TREND_YEARS:
        raise BooksError(f"a trend can lay at most {MAX_TREND_YEARS} years side by side: {text!r}")
    return list(range(first, last + 1))


def trend(conn: sqlite3.Connection, business: sqlite3.Row, years: List[int]) -> Trend:
    period = Period(date(years[0], 1, 1), date(years[-1], 12, 31), f"{years[0]}..{years[-1]}")
    pnl = profit_and_loss(conn, business, period, "year")
    counts: Dict[int, int] = {
        int(row[0]): row[1]
        for row in conn.execute(
            "SELECT substr(t.posted_on, 1, 4), COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id"
            " WHERE a.business_id = ? AND t.category_id IS NULL AND t.posted_on BETWEEN ? AND ?"
            " GROUP BY substr(t.posted_on, 1, 4)",
            (business["id"], period.start.isoformat(), period.end.isoformat()),
        )
    }
    return Trend(business["name"], years, pnl, [counts.get(year, 0) for year in years])


# What each category holds

@dataclass
class CategoryStat:
    """Amounts read as the report reads them: an expense is positive when money went out."""

    code: str
    name: str
    kind: Optional[str]
    rows: int = 0
    total: int = 0
    largest_id: int = 0
    largest_on: str = ""
    largest_amount: int = 0
    largest_description: str = ""

    @property
    def average(self) -> int:
        return divide_cents(self.total, self.rows)


@dataclass
class CategoryStats:
    business: str
    period: Period
    categories: List[CategoryStat]
    waiting: Optional[CategoryStat]


def _as_read(kind: Optional[str], amount_cents: int) -> int:
    """As profit and loss shows it: costs and expenses positive when money went out, returns negative because they
    reduce income. Owner money, transfers and the queue keep the bank's sign."""
    return figure(kind, amount_cents) if kind in ("cogs", "expense") else amount_cents


def category_stats(conn: sqlite3.Connection, business: sqlite3.Row, period: Period) -> CategoryStats:
    rows = conn.execute(
        "SELECT t.id, t.posted_on, t.amount_cents, t.description, c.id AS category_id, c.code, c.name, c.kind"
        " FROM transactions t JOIN accounts a ON a.id = t.account_id LEFT JOIN categories c ON c.id = t.category_id"
        " WHERE a.business_id = ? AND t.posted_on BETWEEN ? AND ? ORDER BY t.posted_on, t.id",
        (business["id"], period.start.isoformat(), period.end.isoformat()),
    ).fetchall()
    found: Dict[Optional[int], CategoryStat] = {}
    largest_raw: Dict[Optional[int], int] = {}
    for row in rows:
        key = row["category_id"]
        stat = found.get(key)
        if stat is None:
            stat = CategoryStat(row["code"] or "", row["name"] or WAITING_LABEL, row["kind"])
            found[key] = stat
        stat.rows += 1
        stat.total += _as_read(row["kind"], row["amount_cents"])
        # The row that moved the most money. On a tie the earlier row stays, since rows arrive in date order.
        if abs(row["amount_cents"]) > largest_raw.get(key, -1):
            largest_raw[key] = abs(row["amount_cents"])
            stat.largest_id = row["id"]
            stat.largest_on = row["posted_on"]
            stat.largest_amount = _as_read(row["kind"], row["amount_cents"])
            stat.largest_description = row["description"]
    waiting = found.pop(None, None)
    ordered = sorted(found.values(), key=lambda s: (KIND_ORDER.index(s.kind), s.code))
    result = CategoryStats(business["name"], period, ordered, waiting)
    net = sum(s.total if s.kind in ("income", "returns") else -s.total for s in ordered if s.kind in KIND_ORDER[:4])
    expected = profit_and_loss(conn, business, period).net[0]
    if net != expected:
        raise AssertionError(f"category totals for {business['name']} ({net}) do not tie to profit and loss ({expected})")
    return result
