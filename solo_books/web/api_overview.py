"""The API's view of the whole books: status, businesses and their charts, accounts, and the check."""
from __future__ import annotations

import sqlite3

from .. import books, db, ledger, reconcile, review, safety, schedule_c
from .api import Request, account_by_id, business_by_id, mapping_json, printed_money
from .routes import api


def _account_json(conn: sqlite3.Connection, account: sqlite3.Row) -> dict:
    summary = ledger.account_summary(conn, account)
    data = {
        "id": account["id"],
        "name": account["name"],
        "business_id": account["business_id"],
        "business": account["business_name"],
        "kind": account["kind"],
        "institution": account["institution"],
        "opening_date": account["opening_date"],
        "rows": summary["rows"],
        "latest_row": summary["latest"],
        "balance_on": summary["balance_on"],
        "reconciled_through": summary["reconciled_through"],
        "has_mapping": bool(account["mapping_json"]),
        # A card's statement prints what is owed; the *_printed figures read that way.
        "printed_as_owed": account["kind"] == "credit_card",
    }
    data.update(printed_money("opening_balance", account, account["opening_balance_cents"]))
    data.update(printed_money("balance", account, summary["balance_cents"]))
    return data


@api("GET", "status")
def status(conn, request: Request) -> dict:
    """What the 'status' command shows: the last backup, what waits for review, and how far each account is reconciled."""
    request.params_named()
    businesses = []
    for business in books.list_businesses(conn):
        accounts = []
        for account in books.list_accounts(conn, business["id"]):
            summary = ledger.account_summary(conn, account)
            accounts.append(
                {
                    "id": account["id"],
                    "name": account["name"],
                    "kind": account["kind"],
                    "rows": summary["rows"],
                    "latest_row": summary["latest"],
                    "reconciled_through": summary["reconciled_through"],
                }
            )
        businesses.append(
            {"id": business["id"], "name": business["name"], "waiting": len(review.queue(conn, business["id"])), "accounts": accounts}
        )
    next_step = None
    if not businesses:
        next_step = "Add a business with the 'business add' command."
    elif not any(business["accounts"] for business in businesses):
        next_step = "Add an account with the 'account add' command."
    return {
        "books": str(request.db_path),
        "last_backup": db.get_meta(conn, "last_backup_at"),
        "waiting": sum(business["waiting"] for business in businesses),
        "businesses": businesses,
        "next": next_step,
    }


@api("GET", "businesses")
def business_list(conn, request: Request) -> dict:
    request.params_named()
    return {
        "businesses": [
            {
                "id": business["id"],
                "name": business["name"],
                "activity": business["activity"],
                "created_at": business["created_at"],
                "accounts": len(books.list_accounts(conn, business["id"])),
                "waiting": len(review.queue(conn, business["id"])),
            }
            for business in books.list_businesses(conn)
        ]
    }


@api("GET", "businesses/{id}/chart")
def business_chart(conn, request: Request) -> dict:
    """The chart of accounts, with each category's line on the latest form unless year= asks for another."""
    query = request.params_named("year", "include_retired")
    business = business_by_id(conn, request.params["id"], status=404)
    year = query.number("year", low=1900, high=2200)
    form = schedule_c.load_form(year) if year else schedule_c.latest_form()
    categories = []
    for category in books.list_categories(conn, business["id"], include_retired=query.flag("include_retired")):
        entry = form.find_by_key(category["schedule_c_key"]) if category["schedule_c_key"] else None
        categories.append(
            {
                "id": category["id"],
                "code": category["code"],
                "name": category["name"],
                "kind": category["kind"],
                "line": entry["line"] if entry else None,
                "line_name": schedule_c.line_label(entry) if entry else None,
                "line_key": category["schedule_c_key"],
                "active": bool(category["active"]),
            }
        )
    return {"business": {"id": business["id"], "name": business["name"]}, "tax_year": form.tax_year, "categories": categories}


@api("GET", "accounts")
def account_list(conn, request: Request) -> dict:
    query = request.params_named("business")
    business_id = query.number("business")
    if business_id is not None:
        business_by_id(conn, business_id)
    return {"accounts": [_account_json(conn, account) for account in books.list_accounts(conn, business_id)]}


@api("GET", "accounts/{id}")
def account_detail(conn, request: Request) -> dict:
    request.params_named()
    account = account_by_id(conn, request.params["id"], status=404)
    data = _account_json(conn, account)
    data["rows_before_opening"] = ledger.rows_before_opening(conn, account)
    data["mapping"] = mapping_json(account["mapping_json"])
    data["reconciliations"] = []
    for recon in reconcile.list_for_account(conn, account["id"]):
        entry = {"id": recon["id"], "from": recon["period_start"], "to": recon["period_end"], "status": recon["status"]}
        entry.update(printed_money("statement_end", account, recon["statement_end_cents"]))
        data["reconciliations"].append(entry)
    return data


@api("GET", "check", needs_conn=False)
def check(request: Request) -> dict:
    """The damage and bookkeeping check. It only reads, and it works on books too damaged to open."""
    request.params_named()
    report = safety.check_books(request.db_path)
    return {
        "books": str(report.path),
        "healthy": report.healthy,
        "problems": report.problems,
        "notes": report.notes,
        "counts": report.counts,
        "schema_version": report.version,
        "reads_up_to": db.schema_version(),
    }
