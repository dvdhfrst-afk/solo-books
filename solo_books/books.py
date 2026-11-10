"""Businesses, their charts of accounts, and the bank and card accounts that feed them."""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import List, Optional

from . import history
from .db import transaction
from .errors import BooksError
from .schedule_c import latest_form, load_form
from .text import clean_cell

DATA_DIR = Path(__file__).resolve().parent / "data"

CATEGORY_KINDS = ("income", "returns", "cogs", "expense", "transfer", "equity")
ACCOUNT_KINDS = ("checking", "savings", "credit_card", "other")
# Kinds that never reach profit and loss or a form line.
OFF_FORM_KINDS = ("transfer", "equity")
MAX_NAME = 120


def _clean_name(name: str, what: str) -> str:
    name = clean_cell(name)
    if not name:
        raise BooksError(f"{what} needs a name")
    if len(name) > MAX_NAME:
        raise BooksError(f"{what}'s name can be at most {MAX_NAME} characters")
    return name


def add_business(conn: sqlite3.Connection, name: str, activity: str = "") -> sqlite3.Row:
    name = _clean_name(name, "a business")
    activity = clean_cell(activity)
    if conn.execute("SELECT 1 FROM businesses WHERE name = ?", (name,)).fetchone():
        raise BooksError(f"there is already a business named {name!r}")
    chart = json.loads((DATA_DIR / "default_chart.json").read_text(encoding="utf-8"))
    with transaction(conn):
        cursor = conn.execute("INSERT INTO businesses (name, activity) VALUES (?, ?)", (name, activity))
        business_id = cursor.lastrowid
        for entry in chart["categories"]:
            conn.execute(
                "INSERT INTO categories (business_id, code, name, kind, schedule_c_key) VALUES (?, ?, ?, ?, ?)",
                (business_id, entry["code"], entry["name"], entry["kind"], entry.get("schedule_c_key")),
            )
        history.record(
            conn, "business-add", f"Added business {name}.", business_id,
            {"name": name, "activity": activity, "categories": len(chart["categories"])},
        )
    return get_business(conn, name)


def list_businesses(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM businesses ORDER BY name").fetchall()


def get_business(conn: sqlite3.Connection, name: Optional[str]) -> sqlite3.Row:
    """Find a business by name. With only one business, the name may be left out."""
    if name is None:
        rows = list_businesses(conn)
        if len(rows) == 1:
            return rows[0]
        if not rows:
            raise BooksError("no businesses yet. Add one with 'business add NAME'.")
        names = ", ".join(r["name"] for r in rows)
        raise BooksError(f"say which business with --business ({names})")
    row = conn.execute("SELECT * FROM businesses WHERE name = ?", (name.strip(),)).fetchone()
    if row is None:
        names = ", ".join(r["name"] for r in list_businesses(conn)) or "none yet"
        raise BooksError(f"no business named {name!r} (have: {names})")
    return row


def add_account(
    conn: sqlite3.Connection,
    business: sqlite3.Row,
    name: str,
    kind: str,
    institution: str = "",
    opening_balance_cents: Optional[int] = None,
    opening_date: Optional[date] = None,
    mapping_json: Optional[str] = None,
) -> sqlite3.Row:
    name = _clean_name(name, "an account")
    institution = clean_cell(institution)
    if kind not in ACCOUNT_KINDS:
        raise BooksError(f"account kind must be one of: {', '.join(ACCOUNT_KINDS)}")
    if (opening_balance_cents is None) != (opening_date is None):
        raise BooksError("give the opening balance and the opening date together")
    if conn.execute("SELECT 1 FROM accounts WHERE name = ?", (name,)).fetchone():
        raise BooksError(f"there is already an account named {name!r}")
    opened = opening_date.isoformat() if opening_date else None
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO accounts (business_id, name, kind, institution, opening_balance_cents, opening_date, mapping_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (business["id"], name, kind, institution, opening_balance_cents, opened, mapping_json),
        )
        history.record(
            conn, "account-add", f"Added account {name} ({kind}) to {business['name']}.", business["id"],
            {
                "account_id": cursor.lastrowid, "name": name, "kind": kind, "institution": institution,
                "opening_balance_cents": opening_balance_cents, "opening_date": opened, "mapping": mapping_json,
            },
        )
    return get_account(conn, name)


def list_accounts(conn: sqlite3.Connection, business_id: Optional[int] = None) -> List[sqlite3.Row]:
    sql = (
        "SELECT a.*, b.name AS business_name FROM accounts a JOIN businesses b ON b.id = a.business_id"
    )
    if business_id is None:
        return conn.execute(sql + " ORDER BY b.name, a.name").fetchall()
    return conn.execute(sql + " WHERE a.business_id = ? ORDER BY a.name", (business_id,)).fetchall()


def get_account(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT a.*, b.name AS business_name FROM accounts a JOIN businesses b ON b.id = a.business_id"
        " WHERE a.name = ?",
        (name.strip(),),
    ).fetchone()
    if row is None:
        names = ", ".join(r["name"] for r in list_accounts(conn)) or "none yet"
        raise BooksError(f"no account named {name!r} (have: {names})")
    return row


def rename_business(conn: sqlite3.Connection, business: sqlite3.Row, new_name: str) -> str:
    new_name = _clean_name(new_name, "a business")
    clash = conn.execute("SELECT 1 FROM businesses WHERE name = ? AND id != ?", (new_name, business["id"])).fetchone()
    if clash:
        raise BooksError(f"there is already a business named {new_name!r}")
    with transaction(conn):
        conn.execute("UPDATE businesses SET name = ? WHERE id = ?", (new_name, business["id"]))
        history.record(
            conn, "business-rename", f"Renamed business {business['name']} to {new_name}.", business["id"],
            {"before": business["name"], "after": new_name},
        )
    return new_name


def delete_business(conn: sqlite3.Connection, business: sqlite3.Row) -> None:
    """Only a business with no accounts can go, so this can never take bank rows with it."""
    accounts = list_accounts(conn, business["id"])
    if accounts:
        names = ", ".join(account["name"] for account in accounts)
        raise BooksError(f"{business['name']} still has accounts: {names}. Delete those first.")
    with transaction(conn):
        rules = conn.execute("DELETE FROM rules WHERE business_id = ?", (business["id"],)).rowcount
        conn.execute("DELETE FROM businesses WHERE id = ?", (business["id"],))
        history.record(
            conn, "business-delete", f"Deleted business {business['name']}, its chart and its {rules} rule(s).",
            business["id"], {"name": business["name"], "activity": business["activity"], "rules": rules},
        )


def rename_account(conn: sqlite3.Connection, account: sqlite3.Row, new_name: str) -> str:
    new_name = _clean_name(new_name, "an account")
    clash = conn.execute("SELECT 1 FROM accounts WHERE name = ? AND id != ?", (new_name, account["id"])).fetchone()
    if clash:
        raise BooksError(f"there is already an account named {new_name!r}")
    with transaction(conn):
        conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (new_name, account["id"]))
        history.record(
            conn, "account-rename", f"Renamed account {account['name']} to {new_name}.", account["business_id"],
            {"account_id": account["id"], "before": account["name"], "after": new_name},
        )
    return new_name


def delete_account(conn: sqlite3.Connection, account: sqlite3.Row, with_rows: bool = False) -> int:
    """Remove an account. Rows go with it only when asked for, and never once a period is locked."""
    count = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account["id"],)).fetchone()[0]
    if count and not with_rows:
        raise BooksError(
            f"{account['name']} holds {count} row(s). Pass --with-rows to delete them too. "
            "Take a backup first: this cannot be undone."
        )
    finished = conn.execute(
        "SELECT COUNT(*) FROM reconciliations WHERE account_id = ? AND status = 'finished'", (account["id"],)
    ).fetchone()[0]
    if finished:
        raise BooksError(f"{account['name']} has {finished} finished reconciliation(s). Reopen them first.")
    with transaction(conn):
        gone = [
            (row["id"], history.snapshot(row), None)
            for row in conn.execute(
                "SELECT t.*, ? AS account_name, c.code AS category_code, c.name AS category_name FROM transactions t"
                " LEFT JOIN categories c ON c.id = t.category_id WHERE t.account_id = ? ORDER BY t.id",
                (account["name"], account["id"]),
            )
        ]
        conn.execute("DELETE FROM accounts WHERE id = ?", (account["id"],))
        history.record(
            conn, "account-delete", f"Deleted account {account['name']} and its {count} row(s).", account["business_id"],
            {"account_id": account["id"], "name": account["name"], "kind": account["kind"], "rows": count}, gone,
        )
    return count


def set_account_mapping(conn: sqlite3.Connection, account: sqlite3.Row, mapping_json: str) -> None:
    with transaction(conn):
        conn.execute("UPDATE accounts SET mapping_json = ? WHERE id = ?", (mapping_json, account["id"]))
        history.record(
            conn, "account-map", f"Set the column mapping of {account['name']}.", account["business_id"],
            {"account_id": account["id"], "account": account["name"], "before": account["mapping_json"], "after": mapping_json},
        )


def set_opening_balance(conn: sqlite3.Connection, account: sqlite3.Row, cents: int, on: date) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE accounts SET opening_balance_cents = ?, opening_date = ? WHERE id = ?",
            (cents, on.isoformat(), account["id"]),
        )
        history.record(
            conn, "account-opening", f"Set the opening balance of {account['name']}.", account["business_id"],
            {
                "account_id": account["id"], "account": account["name"],
                "before": {"cents": account["opening_balance_cents"], "date": account["opening_date"]},
                "after": {"cents": cents, "date": on.isoformat()},
            },
        )


def printed_to_book(account: sqlite3.Row, printed_cents: int) -> int:
    """A card statement prints what you owe as a positive number; the books hold it as negative."""
    return -printed_cents if account["kind"] == "credit_card" else printed_cents


def book_to_printed(account: sqlite3.Row, book_cents: int) -> int:
    return -book_cents if account["kind"] == "credit_card" else book_cents


def list_categories(conn: sqlite3.Connection, business_id: int, include_retired: bool = False) -> List[sqlite3.Row]:
    sql = "SELECT * FROM categories WHERE business_id = ?"
    if not include_retired:
        sql += " AND active = 1"
    return conn.execute(sql + " ORDER BY code", (business_id,)).fetchall()


_SHORTEST_NAME_START = 3


def find_category(conn: sqlite3.Connection, business_id: int, text: str) -> sqlite3.Row:
    """Find a category by code, full name, or a name start of three letters or more that only one category has.

    A letter or two could be a slip of the finger, and booking on one would be silent and wrong.
    """
    wanted = text.strip()
    rows = list_categories(conn, business_id, include_retired=True)
    for row in rows:
        if row["code"] == wanted:
            return row
    lowered = wanted.lower()
    for row in rows:
        if row["name"].lower() == lowered:
            return row
    if len(lowered) < _SHORTEST_NAME_START:
        raise BooksError(f"{text!r} is too short to name a category. Use its code, or at least {_SHORTEST_NAME_START} letters of its name.")
    starts = [row for row in rows if row["name"].lower().startswith(lowered)]
    if len(starts) == 1:
        return starts[0]
    if len(starts) > 1:
        options = ", ".join(f"{r['code']} {r['name']}" for r in starts)
        raise BooksError(f"{text!r} could mean: {options}")
    raise BooksError(f"no category {text!r} in this business. See 'chart list'.")


def require_usable(category: sqlite3.Row) -> None:
    if not category["active"]:
        raise BooksError(f"category {category['code']} {category['name']} is retired")


def resolve_line_key(line_or_key: str, kind: str, year: Optional[int] = None) -> str:
    """Accept a form line number such as 16b, or a stable key such as interest_other."""
    form = load_form(year) if year is not None else latest_form()
    entry = form.find_by_line(line_or_key) or form.find_by_key(line_or_key)
    if entry is None:
        raise BooksError(f"no line {line_or_key!r} on the {form.tax_year} form")
    if entry.get("accepts") is None:
        raise BooksError(f"line {entry['line']} on the {form.tax_year} form is figured from other lines")
    if entry["accepts"] == "inventory":
        raise BooksError(f"line {entry['line']} comes from 'inventory set', not from a category")
    if entry["accepts"] != kind:
        raise BooksError(
            f"line {entry['line']} ({entry['name']}) takes {entry['accepts']} categories, and this one is {kind}"
        )
    return entry["key"]


def add_category(
    conn: sqlite3.Connection,
    business_id: int,
    code: str,
    name: str,
    kind: str,
    line: Optional[str] = None,
    year: Optional[int] = None,
) -> sqlite3.Row:
    code, name = clean_cell(code), _clean_name(name, "a category")
    if not code:
        raise BooksError("a category needs a code and a name")
    if kind not in CATEGORY_KINDS:
        raise BooksError(f"category kind must be one of: {', '.join(CATEGORY_KINDS)}")
    if kind in OFF_FORM_KINDS and line:
        raise BooksError(f"{kind} categories stay off the form, so they cannot have a line")
    key = resolve_line_key(line, kind, year) if line else None
    for row in list_categories(conn, business_id, include_retired=True):
        if row["code"] == code:
            raise BooksError(f"code {code} is already {row['name']}")
        if row["name"].lower() == name.lower():
            raise BooksError(f"name {name!r} is already code {row['code']}")
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO categories (business_id, code, name, kind, schedule_c_key) VALUES (?, ?, ?, ?, ?)",
            (business_id, code, name, kind, key),
        )
        history.record(
            conn, "chart-add", f"Added category {code} {name} ({kind}).", business_id,
            {"category_id": cursor.lastrowid, "code": code, "name": name, "kind": kind, "line_key": key},
        )
    return find_category(conn, business_id, code)


def map_category(conn: sqlite3.Connection, category: sqlite3.Row, line: Optional[str], year: Optional[int] = None) -> None:
    """Point a category at a form line, or take it off the form with line=None."""
    key = None
    if line is not None:
        if category["kind"] in OFF_FORM_KINDS:
            raise BooksError(f"{category['kind']} categories stay off the form")
        key = resolve_line_key(line, category["kind"], year)
    with transaction(conn):
        conn.execute("UPDATE categories SET schedule_c_key = ? WHERE id = ?", (key, category["id"]))
        where = f"line {line}" if line is not None else "no line"
        history.record(
            conn, "chart-map", f"Pointed category {category['code']} {category['name']} at {where}.", category["business_id"],
            {"category_id": category["id"], "code": category["code"], "before": category["schedule_c_key"], "after": key},
        )


def rename_category(conn: sqlite3.Connection, category: sqlite3.Row, new_name: str) -> None:
    new_name = _clean_name(new_name, "a category")
    clash = conn.execute(
        "SELECT code FROM categories WHERE business_id = ? AND name = ? AND id != ?",
        (category["business_id"], new_name, category["id"]),
    ).fetchone()
    if clash:
        raise BooksError(f"name {new_name!r} is already code {clash['code']}")
    with transaction(conn):
        conn.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, category["id"]))
        history.record(
            conn, "chart-rename", f"Renamed category {category['code']} from {category['name']} to {new_name}.",
            category["business_id"],
            {"category_id": category["id"], "code": category["code"], "before": category["name"], "after": new_name},
        )


def retire_category(conn: sqlite3.Connection, category: sqlite3.Row) -> None:
    """Hide a category from pickers. Rows already booked to it stay and still report."""
    in_rules = conn.execute("SELECT COUNT(*) FROM rules WHERE category_id = ?", (category["id"],)).fetchone()[0]
    if in_rules:
        raise BooksError(f"{in_rules} rule(s) still book to this category; change or delete them first")
    with transaction(conn):
        conn.execute("UPDATE categories SET active = 0 WHERE id = ?", (category["id"],))
        history.record(
            conn, "chart-retire", f"Retired category {category['code']} {category['name']}.", category["business_id"],
            {"category_id": category["id"], "code": category["code"], "name": category["name"]},
        )


def set_inventory(conn: sqlite3.Connection, business_id: int, year: int, beginning: int, ending: int) -> None:
    earlier = get_inventory(conn, business_id, year)
    with transaction(conn):
        conn.execute(
            "INSERT INTO inventory_counts (business_id, tax_year, beginning_cents, ending_cents) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (business_id, tax_year) DO UPDATE SET"
            " beginning_cents = excluded.beginning_cents, ending_cents = excluded.ending_cents",
            (business_id, year, beginning, ending),
        )
        history.record(
            conn, "inventory-set", f"Saved inventory counts for {year}.", business_id,
            {
                "year": year,
                "before": {"beginning_cents": earlier["beginning_cents"], "ending_cents": earlier["ending_cents"]} if earlier else None,
                "after": {"beginning_cents": beginning, "ending_cents": ending},
            },
        )


def list_inventory(conn: sqlite3.Connection, business_id: Optional[int] = None) -> List[sqlite3.Row]:
    sql = (
        "SELECT i.*, b.name AS business_name FROM inventory_counts i JOIN businesses b ON b.id = i.business_id"
    )
    if business_id is None:
        return conn.execute(sql + " ORDER BY b.name, i.tax_year").fetchall()
    return conn.execute(sql + " WHERE i.business_id = ? ORDER BY i.tax_year", (business_id,)).fetchall()


def get_inventory(conn: sqlite3.Connection, business_id: int, year: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM inventory_counts WHERE business_id = ? AND tax_year = ?", (business_id, year)
    ).fetchone()
