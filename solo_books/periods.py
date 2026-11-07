"""Named stretches of time: a year, a quarter, a month, or an explicit date range."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import List

from .errors import BooksError

# Wide enough for any real row, and it keeps date arithmetic away from the ends of the calendar.
FIRST_YEAR = 1900
LAST_YEAR = 2200


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def check_year(year: int) -> int:
    if not FIRST_YEAR <= year <= LAST_YEAR:
        raise BooksError(f"year {year} is not one these books can hold ({FIRST_YEAR} to {LAST_YEAR})")
    return year


def parse_iso_date(text: str) -> date:
    try:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", text.strip()):
            raise ValueError
        day = date.fromisoformat(text.strip())
    except ValueError:
        raise BooksError(f"not a date (use YYYY-MM-DD): {text!r}") from None
    check_year(day.year)
    return day


def year_period(year: int) -> Period:
    check_year(year)
    return Period(date(year, 1, 1), date(year, 12, 31), str(year))


def quarter_period(year: int, quarter: int) -> Period:
    first_month = 3 * (quarter - 1) + 1
    last_month = first_month + 2
    last_day = calendar.monthrange(year, last_month)[1]
    return Period(date(year, first_month, 1), date(year, last_month, last_day), f"{year}-Q{quarter}")


def month_period(year: int, month: int) -> Period:
    last_day = calendar.monthrange(year, month)[1]
    return Period(date(year, month, 1), date(year, month, last_day), f"{year}-{month:02d}")


def parse_period(text: str) -> Period:
    """Read 2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20."""
    s = text.strip()
    if ".." in s:
        left, right = s.split("..", 1)
        start, end = parse_iso_date(left), parse_iso_date(right)
        if end < start:
            raise BooksError(f"period ends before it starts: {text!r}")
        return Period(start, end, f"{start.isoformat()}..{end.isoformat()}")
    if re.match(r"^\d{4}$", s):
        return year_period(int(s))
    match = re.match(r"^(\d{4})-[Qq]([1-4])$", s)
    if match:
        return quarter_period(check_year(int(match.group(1))), int(match.group(2)))
    match = re.match(r"^(\d{4})-(0[1-9]|1[0-2])$", s)
    if match:
        return month_period(check_year(int(match.group(1))), int(match.group(2)))
    raise BooksError(f"not a period: {text!r} (use 2025, 2025-Q2, 2025-03, or 2025-01-15..2025-02-20)")


def split_period(period: Period, by: str) -> List[Period]:
    """Cut a period into calendar months, quarters or years, clipped at both ends.

    A bucket clipped by the period keeps its calendar label only when it is whole.
    """
    if by == "total":
        return [Period(period.start, period.end, "Total")]
    if by not in ("month", "quarter", "year"):
        raise BooksError(f"cannot group by {by!r}")

    buckets: List[Period] = []
    year, month = period.start.year, period.start.month
    while date(year, month, 1) <= period.end:
        if by == "month":
            whole = month_period(year, month)
        elif by == "quarter":
            whole = quarter_period(year, (month - 1) // 3 + 1)
        else:
            whole = year_period(year)
        start = max(whole.start, period.start)
        end = min(whole.end, period.end)
        label = whole.label if (start, end) == (whole.start, whole.end) else f"{start.isoformat()}..{end.isoformat()}"
        buckets.append(Period(start, end, label))
        next_day = whole.end.toordinal() + 1
        following = date.fromordinal(next_day)
        year, month = following.year, following.month
    return buckets
