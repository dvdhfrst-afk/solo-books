"""What the API's handlers share: its errors, reading parameters and bodies, and turning rows into JSON.

Statuses follow one plan. 400 is a request that is malformed (bad JSON, a wrong type, a missing or unknown
field). 404 is an id in the path that names nothing. 409 is a write the books refuse because of their state,
such as deleting a row in a finished period. 422 is a well-formed request the bookkeeping rules refuse, with
the same sentence the command line prints, such as a category that does not exist.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, NamedTuple, Optional, Sequence

from .. import books
from ..errors import BooksError
from ..money import format_cents, parse_cents
from ..periods import parse_iso_date

ERROR_CODES = {
    400: "bad_request",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    422: "refused",
    500: "bug",
    503: "unavailable",
}
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
_LARGEST_ID = 10 ** 18


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def error_body(status: int, message: str) -> dict:
    return {"error": {"code": ERROR_CODES[status], "message": message}}


@contextmanager
def refusals_as(status: int) -> Iterator[None]:
    """A BooksError raised inside answers with this status, keeping its sentence."""
    try:
        yield
    except BooksError as exc:
        raise ApiError(status, str(exc)) from None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class Query:
    """The query string. A name it does not know is refused, so a typo cannot quietly widen a search."""

    def __init__(self, raw: Dict[str, List[str]], known: Sequence[str]) -> None:
        unknown = sorted(set(raw) - set(known))
        if unknown:
            raise ApiError(400, f"unknown parameter {unknown[0]!r} (known: {', '.join(known) or 'none'})")
        self._raw = raw

    def text(self, name: str) -> Optional[str]:
        values = self._raw.get(name)
        if not values:
            return None
        if len(values) > 1:
            raise ApiError(400, f"give {name} once")
        return values[0] if values[0].strip() else None

    def number(self, name: str, default: Optional[int] = None, low: int = 1, high: int = _LARGEST_ID) -> Optional[int]:
        value = self.text(name)
        if value is None:
            return default
        if not value.strip().isdigit() or not low <= int(value) <= high:
            raise ApiError(400, f"{name} must be a whole number from {low} to {high}")
        return int(value)

    def flag(self, name: str) -> bool:
        value = (self.text(name) or "false").lower()
        if value not in ("true", "false", "1", "0", "yes", "no"):
            raise ApiError(400, f"{name} must be true or false")
        return value in ("true", "1", "yes")


class Body:
    """A JSON object sent with a write. A field it does not know is refused."""

    def __init__(self, raw: dict, known: Sequence[str]) -> None:
        unknown = sorted(set(raw) - set(known))
        if unknown:
            raise ApiError(400, f"unknown field {unknown[0]!r} (known: {', '.join(known) or 'none'})")
        self._raw = raw

    def require(self, name: str) -> None:
        """The field must be sent, even when null is one of its meanings."""
        if name not in self._raw:
            raise ApiError(400, f"{name} is required")

    def text(self, name: str, required: bool = False) -> Optional[str]:
        value = self._raw.get(name)
        if value is None:
            if required:
                raise ApiError(400, f"{name} is required")
            return None
        if not isinstance(value, str):
            raise ApiError(400, f"{name} must be text")
        return value

    def identifier(self, name: str, required: bool = False) -> Optional[int]:
        value = self._raw.get(name)
        if value is None:
            if required:
                raise ApiError(400, f"{name} is required")
            return None
        if not _is_int(value) or not 1 <= value < _LARGEST_ID:
            raise ApiError(400, f"{name} must be a whole number from 1 up")
        return value

    def number(self, name: str, default: int) -> int:
        value = self._raw.get(name)
        if value is None:
            return default
        if not _is_int(value) or abs(value) >= _LARGEST_ID:
            raise ApiError(400, f"{name} must be a whole number")
        return value

    def flag(self, name: str) -> bool:
        value = self._raw.get(name, False)
        if not isinstance(value, bool):
            raise ApiError(400, f"{name} must be true or false")
        return value

    def amount(self, name: str, required: bool = False) -> Optional[int]:
        """Money is sent as text, the way a statement prints it, such as "-15.00", so it never passes through a float."""
        text = self.text(name, required)
        if text is None or not text.strip():
            if required:
                raise ApiError(400, f"{name} is required")
            return None
        return parse_cents(text)

    def day(self, name: str):
        return parse_iso_date(self.text(name, required=True) or "")


class Request(NamedTuple):
    params: Dict[str, int]
    query: Dict[str, List[str]]
    # A JSON object for most writes, the raw bytes for an upload, and None for a read.
    body: Any
    db_path: Path
    # The settings the 'web' command started with, or None for a server started some other way.
    settings: Any = None

    def fields(self, *known: str) -> Body:
        return Body(self.body if isinstance(self.body, dict) else {}, known)

    def params_named(self, *known: str) -> Query:
        return Query(self.query, known)


# Looking things up by id

def business_by_id(conn: sqlite3.Connection, business_id: int, status: int = 422) -> sqlite3.Row:
    for row in books.list_businesses(conn):
        if row["id"] == business_id:
            return row
    raise ApiError(status, f"no business {business_id}")


def account_by_id(conn: sqlite3.Connection, account_id: int, status: int = 422) -> sqlite3.Row:
    for row in books.list_accounts(conn):
        if row["id"] == account_id:
            return row
    raise ApiError(status, f"no account {account_id}")


def chosen_business(conn: sqlite3.Connection, business_id: Optional[int], field: str = "business_id") -> sqlite3.Row:
    """The business named by id. With only one business, the id may be left out, as on the command line."""
    if business_id is not None:
        return business_by_id(conn, business_id)
    rows = books.list_businesses(conn)
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise ApiError(422, "no businesses yet. Add one with 'business add NAME'.")
    names = ", ".join(f"{row['id']} {row['name']}" for row in rows)
    raise ApiError(400, f"say which business with {field} ({names})")


# Turning rows into JSON. Every amount goes out as whole cents and as text for reading.

def money(name: str, cents: Optional[int]) -> dict:
    return {f"{name}_cents": cents, name: None if cents is None else format_cents(cents)}


def printed_money(name: str, account: sqlite3.Row, cents: Optional[int]) -> dict:
    """Book cents, plus the figure as a statement prints it: on a card, what is owed."""
    shown = None if cents is None else format_cents(books.book_to_printed(account, cents))
    return dict(money(name, cents), **{f"{name}_printed": shown})


def category_json(category: Optional[sqlite3.Row]) -> Optional[dict]:
    if category is None:
        return None
    return {"id": category["id"], "code": category["code"], "name": category["name"], "kind": category["kind"]}


def booked_category(row: sqlite3.Row) -> Optional[dict]:
    """The category a transaction row is booked to, from the columns ledger's queries join in."""
    if row["category_id"] is None:
        return None
    return {"id": row["category_id"], "code": row["category_code"], "name": row["category_name"], "kind": row["category_kind"]}


def transaction_json(row: sqlite3.Row, tag_names: Sequence[str] = ()) -> dict:
    """A row, with the names of its tags, which the caller looks up for all its rows at once."""
    data = {
        "id": row["id"],
        "date": row["posted_on"],
        "account_id": row["account_id"],
        "account": row["account_name"],
        "business_id": row["business_id"],
        "business": row["business_name"],
        "description": row["description"],
        "memo": row["memo"],
        "merchant": row["merchant"],
        "reference": row["reference"],
        "category": booked_category(row),
        "booked_by": row["category_source"],
        "rule_id": row["rule_id"],
        "reconciliation_id": row["reconciliation_id"],
        "import_id": row["import_id"],
        "note": row["note"],
        "tags": list(tag_names),
    }
    data.update(money("amount", row["amount_cents"]))
    return data


def mapping_json(text: Optional[str]) -> Optional[dict]:
    return json.loads(text) if text else None
