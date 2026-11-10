"""Working out any report from what was asked for, once, for the command line and the web app alike.

A caller names the report and gives its choices: the businesses, a period, a year or a run of years, how to group,
and a tag. build() works out one result per business and returns the Report every format renders.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Dict, List, Optional

from . import analysis, budgets, payees, reports, tags
from .errors import BooksError
from .formats.base import Report
from .money import parse_cents
from .periods import Period, parse_period, year_period

# Reports that add rows up, and so can be limited to one tag's rows.
TAGGABLE = ("pnl", "cashflow", "categories", "trend", "budget", "payees")
# What each report is asked for by: a period, one year, or a run of years.
PERIOD_REPORTS = ("pnl", "balances", "cashflow", "categories", "budget")
YEAR_REPORTS = ("schedule-c", "payees", "contractors")
GROUPINGS = ("total", "month", "quarter", "year")
GROUPED = ("pnl", "cashflow")
PERIOD_HELP = "2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20"
DEFAULT_PAYEE_LIMIT = 10


def build(
    conn: sqlite3.Connection,
    kind: str,
    businesses: List[sqlite3.Row],
    period: Optional[str] = None,
    year: Optional[int] = None,
    years: Optional[str] = None,
    by: str = "total",
    tag: Optional[str] = None,
    limit: int = DEFAULT_PAYEE_LIMIT,
    threshold: Optional[str] = None,
    lines_year: Optional[int] = None,
    all_lines: bool = False,
    show_date: Callable[[object], str] = str,
) -> Report:
    """One report for these businesses. A business named on its own must carry the tag; with several, those that do."""
    if not businesses:
        raise BooksError("no businesses yet. Add one with 'business add NAME'.")
    if tag and kind not in TAGGABLE:
        raise BooksError(f"report {kind} cannot be limited to a tag (these can: {', '.join(TAGGABLE)})")
    if by not in GROUPINGS:
        raise BooksError(f"group by one of {', '.join(GROUPINGS)}, not {by!r}")
    tagged: Dict[int, sqlite3.Row] = {}
    tag_name = None
    if tag:
        tagged = tags.tags_named(conn, tag, businesses[0]["id"] if len(businesses) == 1 else None)
        businesses = [business for business in businesses if business["id"] in tagged]
        tag_name = tagged[businesses[0]["id"]]["name"]

    def tag_of(business: sqlite3.Row) -> Optional[int]:
        return tagged[business["id"]]["id"] if tagged else None

    span: Optional[Period] = None
    if kind in PERIOD_REPORTS:
        if not period:
            raise BooksError(f"report {kind} needs a period, such as {PERIOD_HELP}")
        span = parse_period(period)
    elif kind in YEAR_REPORTS:
        if year is None:
            raise BooksError(f"report {kind} needs a year")
        span = year_period(year)
    elif kind == "trend":
        if not years:
            raise BooksError("report trend needs years, such as 2025 or 2023..2025")
        run = analysis.parse_years(years)
    else:
        raise BooksError(f"no report called {kind!r}")
    parts: list
    if kind == "pnl":
        parts = [reports.profit_and_loss(conn, b, span, by, tag_of(b)) for b in businesses]
    elif kind == "balances":
        parts = [reports.balances(conn, b, span) for b in businesses]
    elif kind == "schedule-c":
        parts = [reports.schedule_c(conn, b, year, lines_year) for b in businesses]
    elif kind == "cashflow":
        parts = [analysis.cashflow(conn, b, span, by, tag_of(b)) for b in businesses]
    elif kind == "categories":
        parts = [analysis.category_stats(conn, b, span, tag_of(b)) for b in businesses]
    elif kind == "trend":
        parts = [analysis.trend(conn, b, run, tag_of(b)) for b in businesses]
    elif kind == "budget":
        parts = [budgets.budget_report(conn, b, span, tag_of(b)) for b in businesses]
    elif kind == "payees":
        parts = [payees.payee_report(conn, b, span, tag_of(b)) for b in businesses]
    else:
        cents = parse_cents(threshold or payees.DEFAULT_THRESHOLD)
        parts = [payees.contractor_report(conn, b, span, cents) for b in businesses]
    return Report(kind, parts, span, all_lines=all_lines, tag=tag_name, limit=limit, show_date=show_date)
