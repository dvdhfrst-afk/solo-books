"""The history of changes through the API: listing them, showing one, and taking one back."""
from __future__ import annotations

import sqlite3

from .. import history
from ..periods import parse_period
from .api import DEFAULT_LIMIT, MAX_LIMIT, Request, business_by_id, refusals_as
from .routes import api


def change_json(entry: sqlite3.Row) -> dict:
    return {
        "id": entry["id"],
        "changed_at": entry["changed_at"],
        "source": entry["source"],
        "kind": entry["kind"],
        "business_id": entry["business_id"],
        "business": entry["business_name"],
        "rows": entry["row_count"],
        "summary": entry["summary"],
        "reverted_by": entry["reverted_by"],
        "revertible": entry["kind"] in history.REVERTIBLE and entry["reverted_by"] is None,
    }


@api("GET", "history")
def history_list(conn, request: Request) -> dict:
    """Newest first, filtered the way 'history list' filters."""
    query = request.params_named("period", "kind", "row", "business", "limit")
    business_id = query.number("business")
    if business_id is not None:
        business_by_id(conn, business_id)
    period = query.text("period")
    entries = history.list_entries(
        conn,
        period=parse_period(period) if period else None,
        kind=query.text("kind"),
        transaction_id=query.number("row"),
        business_id=business_id,
        limit=query.number("limit", DEFAULT_LIMIT, high=MAX_LIMIT),
    )
    return {"changes": [change_json(entry) for entry in entries]}


def _detail(conn, entry_id: int) -> dict:
    with refusals_as(404):
        entry = history.get_entry(conn, entry_id)
    data = change_json(entry)
    data["kind_means"] = history.KINDS.get(entry["kind"], "")
    data["detail"] = history.entry_detail(entry)
    data["row_changes"] = [
        {
            "transaction_id": transaction_id,
            "before": before,
            "after": after,
            "before_text": history.describe(before),
            "after_text": history.describe(after),
        }
        for transaction_id, before, after in history.entry_rows(conn, entry_id)
    ]
    return data


@api("GET", "history/{id}")
def history_show(conn, request: Request) -> dict:
    request.params_named()
    return {"change": _detail(conn, request.params["id"])}


@api("POST", "history/{id}/revert")
def history_revert(conn, request: Request) -> dict:
    """Take one change back, as 'history revert' does. A change that is not safe to undo is refused with 409."""
    request.fields()
    entry_id = request.params["id"]
    with refusals_as(404):
        history.get_entry(conn, entry_id)
    with refusals_as(409):
        new_id, summary = history.revert(conn, entry_id)
    return {"reverted": entry_id, "summary": summary, "change": _detail(conn, new_id)}
