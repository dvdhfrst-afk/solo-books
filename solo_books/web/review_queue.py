"""The review queue page and the JSON it reads and sends."""
from __future__ import annotations

from pathlib import Path

from ..books import find_category, list_businesses, list_categories
from ..db import transaction
from ..errors import BooksError
from ..ledger import get_transaction, uncategorize
from ..money import format_cents
from ..review import book_rows, group_rows, queue
from ..rules import delete_rule
from ..text import names_a_payee
from .routes import get, is_id_list, page, post

PAGE_PATH = Path(__file__).resolve().parent.parent / "data" / "review.html"


@page("/")
def review_page(token: str, nonce: str) -> str:
    return PAGE_PATH.read_text(encoding="utf-8").replace("__TOKEN__", token).replace("__NONCE__", nonce)


@get("/api/queue")
def queue_payload(conn) -> dict:
    rows = queue(conn)
    groups = group_rows(rows)
    businesses = []
    for business in list_businesses(conn):
        businesses.append(
            {
                "id": business["id"],
                "name": business["name"],
                "categories": [
                    {"code": c["code"], "name": c["name"], "kind": c["kind"]}
                    for c in list_categories(conn, business["id"])
                ],
            }
        )
    return {
        "remaining": len(rows),
        "businesses": businesses,
        "groups": [
            {
                "business_id": group.business_id,
                "business": group.business,
                "merchant": group.merchant,
                "total": format_cents(group.total_cents),
                "can_make_rule": names_a_payee(group.merchant),
                "rows": [
                    {
                        "id": row["id"],
                        "date": row["posted_on"],
                        "account": row["account_name"],
                        "amount": format_cents(row["amount_cents"]),
                        "description": row["description"],
                    }
                    for row in group.rows
                ],
            }
            for group in groups
        ],
    }


@post("/api/categorize")
def categorize_request(conn, body: dict) -> dict:
    ids = body.get("ids")
    if not is_id_list(ids) or not ids:
        raise BooksError("ids must be a list of transaction numbers")
    if not isinstance(body.get("category"), str):
        raise BooksError("category is required")
    business_id = get_transaction(conn, ids[0])["business_id"]
    category = find_category(conn, business_id, body["category"])
    pattern = body.get("rule_pattern") or None
    if pattern is not None and not isinstance(pattern, str):
        raise BooksError("rule_pattern must be text")
    unticked = body.get("unticked_ids", [])
    if not is_id_list(unticked):
        raise BooksError("unticked_ids must be a list of transaction numbers")
    result = book_rows(conn, business_id, ids, category, rule_pattern=pattern, held_back=unticked)
    result["category"] = f"{category['code']} {category['name']}"
    result["ids"] = ids
    return result


@post("/api/undo")
def undo_request(conn, body: dict) -> dict:
    """Put rows back in the queue; if the action made a rule, remove it and what it booked."""
    ids = body.get("ids")
    if not is_id_list(ids):
        raise BooksError("ids must be a list of transaction numbers")
    rule_id = body.get("rule_id")
    if rule_id is not None and (not isinstance(rule_id, int) or isinstance(rule_id, bool)):
        raise BooksError("rule_id must be a number")
    with transaction(conn):
        count = uncategorize(conn, ids)
        if rule_id is not None:
            booked = conn.execute("SELECT id FROM transactions WHERE rule_id = ?", (rule_id,)).fetchall()
            count += uncategorize(conn, [row["id"] for row in booked])
            delete_rule(conn, rule_id)
    return {"returned": count}
