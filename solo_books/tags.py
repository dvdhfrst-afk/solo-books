"""Tags: free-form labels on rows, such as a client or a project, that cut across categories.

A tag belongs to one business. Its name is compared without regard to case, so "Harbor" and "HARBOR" are one tag.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Dict, Iterable, List, Optional, Tuple

from . import history
from .db import transaction
from .errors import BooksError
from .ledger import get_transaction

MAX_TAG = 40
_CONTROL = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u200e\\u200f\\u2028\\u2029\\u202a-\\u202e\\u2066-\\u2069]")


def clean_tag(name: str) -> str:
    """A tag is one short line of text. Control characters are refused rather than quietly dropped."""
    if _CONTROL.search(name):
        raise BooksError("a tag cannot hold control characters, tabs or line breaks")
    text = " ".join(name.split())
    if not text:
        raise BooksError("a tag needs a name")
    if len(text) > MAX_TAG:
        raise BooksError(f"a tag can be at most {MAX_TAG} characters")
    return text


def find_tag(conn: sqlite3.Connection, business_id: int, name: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tags WHERE business_id = ? AND name = ?", (business_id, clean_tag(name))).fetchone()
    if row is None:
        known = [r["name"] for r in conn.execute("SELECT name FROM tags WHERE business_id = ? ORDER BY name", (business_id,))]
        raise BooksError(f"no tag {name!r} in this business (have: {', '.join(known) or 'none yet'})")
    return row


def tags_named(conn: sqlite3.Connection, name: str, business_id: Optional[int] = None) -> Dict[int, sqlite3.Row]:
    """The tag of this name in one business, or in every business that has one, by business."""
    text = clean_tag(name)
    if business_id is not None:
        return {business_id: find_tag(conn, business_id, text)}
    found = {row["business_id"]: row for row in conn.execute("SELECT * FROM tags WHERE name = ?", (text,))}
    if not found:
        raise BooksError(f"no tag {name!r} in any business. See 'tag list'.")
    return found


def _rows_of_one_business(conn: sqlite3.Connection, transaction_ids: Iterable[int]) -> Tuple[int, List[int]]:
    ids: List[int] = []
    business_id = None
    for transaction_id in transaction_ids:
        if transaction_id in ids:
            continue
        row = get_transaction(conn, transaction_id)
        if business_id is not None and row["business_id"] != business_id:
            raise BooksError(
                f"transaction {transaction_id} is in a different business from transaction {ids[0]}. Tag one business at a time."
            )
        business_id = row["business_id"]
        ids.append(transaction_id)
    if business_id is None:
        raise BooksError("name at least one transaction")
    return business_id, ids


def _tagged(conn: sqlite3.Connection, tag_id: int, transaction_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM transaction_tags WHERE tag_id = ? AND transaction_id = ?", (tag_id, transaction_id)
    ).fetchone() is not None


def add_tag(conn: sqlite3.Connection, name: str, transaction_ids: Iterable[int]) -> Dict:
    """Tag rows, making the tag if this business has none of that name yet."""
    text = clean_tag(name)
    business_id, ids = _rows_of_one_business(conn, transaction_ids)
    with transaction(conn):
        tag = conn.execute("SELECT * FROM tags WHERE business_id = ? AND name = ?", (business_id, text)).fetchone()
        created = tag is None
        if created:
            conn.execute("INSERT INTO tags (business_id, name) VALUES (?, ?)", (business_id, text))
            tag = conn.execute("SELECT * FROM tags WHERE business_id = ? AND name = ?", (business_id, text)).fetchone()
        fresh = [transaction_id for transaction_id in ids if not _tagged(conn, tag["id"], transaction_id)]
        conn.executemany(
            "INSERT INTO transaction_tags (tag_id, transaction_id) VALUES (?, ?)", [(tag["id"], tid) for tid in fresh]
        )
        if fresh or created:
            history.record(
                conn, "tag-add", f"Tagged {len(fresh)} row(s) {tag['name']}" + (" (a new tag)." if created else "."),
                business_id, {"tag_id": tag["id"], "tag": tag["name"], "created": created},
                [(tid, {"tag": tag["name"], "tagged": False}, {"tag": tag["name"], "tagged": True}) for tid in fresh],
            )
    return {"tag": tag["name"], "added": len(fresh), "already": len(ids) - len(fresh), "created": created}


def remove_tag(conn: sqlite3.Connection, name: str, transaction_ids: Iterable[int]) -> Dict:
    business_id, ids = _rows_of_one_business(conn, transaction_ids)
    tag = find_tag(conn, business_id, name)
    with transaction(conn):
        held = [transaction_id for transaction_id in ids if _tagged(conn, tag["id"], transaction_id)]
        conn.executemany(
            "DELETE FROM transaction_tags WHERE tag_id = ? AND transaction_id = ?", [(tag["id"], tid) for tid in held]
        )
        if held:
            history.record(
                conn, "tag-remove", f"Took tag {tag['name']} off {len(held)} row(s).", business_id,
                {"tag_id": tag["id"], "tag": tag["name"]},
                [(tid, {"tag": tag["name"], "tagged": True}, {"tag": tag["name"], "tagged": False}) for tid in held],
            )
    return {"tag": tag["name"], "removed": len(held), "not_tagged": len(ids) - len(held)}


def rename_tag(conn: sqlite3.Connection, business_id: int, name: str, new_name: str) -> str:
    tag = find_tag(conn, business_id, name)
    text = clean_tag(new_name)
    clash = conn.execute(
        "SELECT name FROM tags WHERE business_id = ? AND name = ? AND id != ?", (business_id, text, tag["id"])
    ).fetchone()
    if clash:
        raise BooksError(f"there is already a tag {clash['name']!r}. Tags are the same whatever their capitals.")
    with transaction(conn):
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", (text, tag["id"]))
        history.record(
            conn, "tag-rename", f"Renamed tag {tag['name']} to {text}.", business_id,
            {"tag_id": tag["id"], "before": tag["name"], "after": text},
        )
    return text


def delete_tag(conn: sqlite3.Connection, business_id: int, name: str) -> int:
    """Take a tag off every row and forget it. The rows themselves do not change."""
    tag = find_tag(conn, business_id, name)
    with transaction(conn):
        held = [row[0] for row in conn.execute("SELECT transaction_id FROM transaction_tags WHERE tag_id = ? ORDER BY transaction_id", (tag["id"],))]
        conn.execute("DELETE FROM tags WHERE id = ?", (tag["id"],))
        history.record(
            conn, "tag-delete", f"Deleted tag {tag['name']}, which was on {len(held)} row(s).", business_id,
            {"tag_id": tag["id"], "tag": tag["name"]},
            [(tid, {"tag": tag["name"], "tagged": True}, {"tag": tag["name"], "tagged": False}) for tid in held],
        )
    return len(held)


def list_tags(conn: sqlite3.Connection, business_id: Optional[int] = None) -> List[sqlite3.Row]:
    """Each tag with how many rows carry it and the money in and out on them."""
    sql = (
        "SELECT g.id, g.name, g.business_id, b.name AS business_name, COUNT(t.id) AS row_count,"
        " COALESCE(SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents END), 0) AS money_in,"
        " COALESCE(-SUM(CASE WHEN t.amount_cents < 0 THEN t.amount_cents END), 0) AS money_out,"
        " SUM(CASE WHEN t.id IS NOT NULL AND t.category_id IS NULL THEN 1 ELSE 0 END) AS waiting"
        " FROM tags g JOIN businesses b ON b.id = g.business_id"
        " LEFT JOIN transaction_tags tt ON tt.tag_id = g.id"
        " LEFT JOIN transactions t ON t.id = tt.transaction_id"
    )
    params: tuple = ()
    if business_id is not None:
        sql += " WHERE g.business_id = ?"
        params = (business_id,)
    return conn.execute(sql + " GROUP BY g.id ORDER BY b.name, g.name", params).fetchall()


def tags_of(conn: sqlite3.Connection, transaction_id: int) -> List[str]:
    return [
        row["name"]
        for row in conn.execute(
            "SELECT g.name FROM transaction_tags tt JOIN tags g ON g.id = tt.tag_id WHERE tt.transaction_id = ? ORDER BY g.name",
            (transaction_id,),
        )
    ]


def problems(conn: sqlite3.Connection) -> List[str]:
    """What 'check' reports about tags: a tag on another business's row, or a name no command would accept."""
    found: List[str] = []
    crossed = conn.execute(
        "SELECT COUNT(*) FROM transaction_tags tt JOIN tags g ON g.id = tt.tag_id"
        " JOIN transactions t ON t.id = tt.transaction_id JOIN accounts a ON a.id = t.account_id"
        " WHERE a.business_id != g.business_id"
    ).fetchone()[0]
    if crossed:
        found.append(f"{crossed} row(s) carry a tag that belongs to another business.")
    bad = 0
    for row in conn.execute("SELECT name FROM tags"):
        try:
            if clean_tag(row["name"]) != row["name"]:
                bad += 1
        except BooksError:
            bad += 1
    if bad:
        found.append(f"{bad} tag(s) have a name that is empty, too long or holds control characters.")
    return found
