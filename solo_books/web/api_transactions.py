"""Rows through the API: finding them, booking them, notes, adding a row by hand, and deleting one."""
from __future__ import annotations

import sqlite3
from typing import Optional

from .. import books, ledger
from ..money import format_cents
from ..periods import parse_period
from ..rules import list_rules
from .api import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ApiError,
    Request,
    account_by_id,
    business_by_id,
    money,
    refusals_as,
    transaction_json,
)
from .routes import api


def _row(conn: sqlite3.Connection, transaction_id: int) -> sqlite3.Row:
    with refusals_as(404):
        return ledger.get_transaction(conn, transaction_id)


def _category_filter(conn, query, business_id: Optional[int], account_id: Optional[int]) -> Optional[int]:
    """A category is named by code or name inside one business, the same way 'txn list --category' reads it."""
    text = query.text("category")
    if text is None:
        return None
    if business_id is None and account_id is not None:
        business_id = account_by_id(conn, account_id)["business_id"]
    if business_id is None:
        rows = books.list_businesses(conn)
        if len(rows) != 1:
            raise ApiError(400, "say which business the category belongs to with business=ID")
        business_id = rows[0]["id"]
    return books.find_category(conn, business_id, text)["id"]


@api("GET", "transactions")
def transaction_list(conn, request: Request) -> dict:
    query = request.params_named(
        "business", "account", "period", "category", "uncategorized", "search", "limit", "offset", "order"
    )
    business_id = query.number("business")
    account_id = query.number("account")
    if business_id is not None:
        business_by_id(conn, business_id)
    if account_id is not None:
        account_by_id(conn, account_id)
    uncategorized = query.flag("uncategorized")
    if uncategorized and query.text("category") is not None:
        raise ApiError(400, "give category or uncategorized, not both")
    order = query.text("order") or "oldest"
    if order not in ("oldest", "newest"):
        raise ApiError(400, "order must be oldest or newest")
    period = query.text("period")
    rows = ledger.find_transactions(
        conn,
        business_id=business_id,
        account_id=account_id,
        period=parse_period(period) if period else None,
        category_id=_category_filter(conn, query, business_id, account_id),
        uncategorized=uncategorized,
        search=query.text("search"),
    )
    if order == "newest":
        rows = rows[::-1]
    limit = query.number("limit", DEFAULT_LIMIT, high=MAX_LIMIT)
    offset = query.number("offset", 0, low=0)
    data = {"total": len(rows), "offset": offset, "limit": limit}
    data.update(money("net", sum(row["amount_cents"] for row in rows)))
    data["transactions"] = [transaction_json(row) for row in rows[offset : offset + limit]]
    return data


def _detail(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Everything 'txn show' says about a row: how it was booked, which files held it, and its note."""
    data = transaction_json(row)
    data["held_back_from_rules"] = ledger.rules_holding_back(conn, row["id"])
    data["imports"] = [{"id": source["id"], "file_name": source["file_name"]} for source in ledger.live_imports_holding(conn, row["id"])]
    data["rule"] = None
    if row["rule_id"] is not None:
        for rule in list_rules(conn, row["business_id"]):
            if rule["id"] == row["rule_id"]:
                data["rule"] = {"id": rule["id"], "match": rule["match_type"], "pattern": rule["pattern"]}
    return data


@api("GET", "transactions/{id}")
def transaction_detail(conn, request: Request) -> dict:
    request.params_named()
    return _detail(conn, _row(conn, request.params["id"]))


@api("POST", "transactions/{id}/category")
def transaction_category(conn, request: Request) -> dict:
    """Book a row by hand, or send it back to the review queue with "category": null."""
    fields = request.fields("category")
    fields.require("category")
    row = _row(conn, request.params["id"])
    wanted = fields.text("category")
    if wanted is None:
        ledger.uncategorize(conn, [row["id"]])
    else:
        ledger.categorize(conn, [row["id"]], books.find_category(conn, row["business_id"], wanted))
    return {"transaction": _detail(conn, ledger.get_transaction(conn, row["id"]))}


@api("POST", "transactions/{id}/note")
def transaction_note(conn, request: Request) -> dict:
    fields = request.fields("note")
    row = _row(conn, request.params["id"])
    ledger.set_note(conn, row["id"], fields.text("note", required=True) or "")
    return {"transaction": _detail(conn, ledger.get_transaction(conn, row["id"]))}


@api("POST", "transactions")
def transaction_add(conn, request: Request):
    """Add a row by hand, as 'txn add' does. A row an export later brings in is not doubled."""
    fields = request.fields("account_id", "date", "amount", "description", "category", "note")
    account = account_by_id(conn, fields.identifier("account_id", required=True))
    day = fields.day("date")
    amount = fields.amount("amount", required=True)
    description = fields.text("description", required=True) or ""
    wanted = fields.text("category")
    category = books.find_category(conn, account["business_id"], wanted) if wanted else None
    new_id = ledger.add_manual(conn, account, day, amount, description, category, fields.text("note") or "")
    warnings = []
    finished = ledger.finished_reconciliation_on(conn, account["id"], day)
    if finished:
        warnings.append(f"this date is inside finished reconciliation {finished}, so that period no longer ties.")
    return 201, {"transaction": _detail(conn, ledger.get_transaction(conn, new_id)), "warnings": warnings}


@api("DELETE", "transactions/{id}")
def transaction_delete(conn, request: Request) -> dict:
    """Delete a row and remember it, so an overlapping export does not bring it back. A finished period refuses."""
    request.params_named()
    row = _row(conn, request.params["id"])
    with refusals_as(409):
        ledger.delete_transaction(conn, row["id"])
    return {"deleted": row["id"], "amount": format_cents(row["amount_cents"]), "description": row["description"]}
