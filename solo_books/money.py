"""Money as integer cents. Nothing in this project touches a float."""
from __future__ import annotations

import re

from .errors import BooksError

_NUMBER = re.compile(r"^(\d{1,3}(?:,\d{3})+|\d+)?(?:\.(\d*))?$")


def parse_cents(text: str) -> int:
    """Turn what a bank or a person writes into cents.

    Accepts 12.34, -12.34, $1,234.56, -$5, $-5, (12.34), 12.34-, +3 and .50.
    Refuses anything that would have to be rounded, such as 1.005.
    """
    original = text
    s = text.strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    if s.endswith("-"):
        negative = not negative
        s = s[:-1].strip()
    if s[:1] == "$":
        s = s[1:].strip()
    if s[:1] in ("-", "+"):
        if s[0] == "-":
            negative = not negative
        s = s[1:].strip()
    if s[:1] == "$":
        s = s[1:].strip()

    match = _NUMBER.match(s)
    if not s or not match or (match.group(1) is None and not match.group(2)):
        raise BooksError(f"not an amount: {original!r}")
    whole = int((match.group(1) or "0").replace(",", ""))
    fraction = match.group(2) or ""
    if len(fraction) > 2:
        if fraction[2:].strip("0"):
            raise BooksError(f"amount has fractions of a cent: {original!r}")
        fraction = fraction[:2]
    cents = whole * 100 + int(fraction.ljust(2, "0"))
    return -cents if negative else cents


def format_cents(cents: int) -> str:
    """1234567 becomes 12,345.67 for reading."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{fraction:02d}"


def plain_cents(cents: int) -> str:
    """1234567 becomes 12345.67 for a CSV cell a spreadsheet reads as a number."""
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{whole}.{fraction:02d}"
