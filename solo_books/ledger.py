"""Rows in the books: finding, adding, deleting and categorizing them."""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Iterable, List, Optional

from .books import require_usable
from .db import transaction
from .errors import BooksError
from .periods import Period
from .money import MAX_CENTS
from .text import clean_cell, merchant_key, normalize_description

_SELECT = (
    "SELECT t.*, a.name AS account_name, a.business_id AS business_id, b.name AS business_name,"
    " c.code AS category_code, c.name AS category_name, c.kind AS category_kind,"
    " c.schedule_c_key AS schedule_c_key"
    " FROM transactions t"
    " JOIN accounts a ON a.id = t.account_id"
    " JOIN businesses b ON b.id = a.business_id"
    " LEFT JOIN categories c ON c.id = t.category_id"
)


def natural_key(posted_on: date, amount_cents: int, description: str) -> str:
    """A row's date, amount and text, ignoring case and spacing."""
    return f"{posted_on.isoformat()}|{amount_cents}|{normalize_description(description)}"


def reference_key(reference: str) -> str:
    return f"ref:{reference.strip()}"


def plain_key(natural: str) -> str:
    return f"row:{natural}"


def finished_reconciliation_on(conn: sqlite3.Connection, account_id: int, day: date) -> Optional[int]:
    """The finished reconciliation whose period holds this day, if any."""
    row = conn.execute(
        "SELECT id FROM reconciliations WHERE account_id = ? AND status = 'finished'"
        " AND period_start <= ? AND period_end >= ?",
        (account_id, day.isoformat(), day.isoformat()),
    ).fetchone()
    return row["id"] if row else None


def get_transaction(conn: sqlite3.Connection, transaction_id: int) -> sqlite3.Row:
    row = conn.execute(_SELECT + " WHERE t.id = ?", (transaction_id,)).fetchone()
    if row is None:
        raise BooksError(f"no transaction {transaction_id}")
    return row


def live_imports_holding(conn: sqlite3.Connection, transaction_id: int) -> List[sqlite3.Row]:
    """The imports not undone whose files held this row, oldest first."""
    return conn.execute(
        "SELECT i.id, i.file_name FROM import_rows r JOIN imports i ON i.id = r.import_id"
        " WHERE r.transaction_id = ? AND i.undone_at IS NULL ORDER BY i.id",
        (transaction_id,),
    ).fetchall()


def rules_holding_back(conn: sqlite3.Connection, transaction_id: int) -> List[int]:
    rows = conn.execute(
        "SELECT rule_id FROM rule_exceptions WHERE transaction_id = ? ORDER BY rule_id", (transaction_id,)
    ).fetchall()
    return [row["rule_id"] for row in rows]


def find_transactions(
    conn: sqlite3.Connection,
    business_id: Optional[int] = None,
    account_id: Optional[int] = None,
    period: Optional[Period] = None,
    category_id: Optional[int] = None,
    uncategorized: bool = False,
    search: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[sqlite3.Row]:
    where, params = [], []  # type: ignore[var-annotated]
    if business_id is not None:
        where.append("a.business_id = ?")
        params.append(business_id)
    if account_id is not None:
        where.append("t.account_id = ?")
        params.append(account_id)
    if period is not None:
        where.append("t.posted_on BETWEEN ? AND ?")
        params.extend([period.start.isoformat(), period.end.isoformat()])
    if category_id is not None:
        where.append("t.category_id = ?")
        params.append(category_id)
    if uncategorized:
        where.append("t.category_id IS NULL")
    if search:
        where.append("(t.description LIKE ? OR t.memo LIKE ? OR t.note LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    sql = _SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.posted_on, t.id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def add_manual(
    conn: sqlite3.Connection,
    account: sqlite3.Row,
    posted_on: date,
    amount_cents: int,
    description: str,
    category: Optional[sqlite3.Row] = None,
    note: str = "",
) -> int:
    """Add a row by hand. If the same row later arrives in an export, it is not doubled."""
    description = _check_entry(amount_cents, description)
    if category is not None:
        _check_category(account["business_id"], category)
    natural = natural_key(posted_on, amount_cents, description)
    key = plain_key(natural)
    with transaction(conn):
        taken = conn.execute(
            "SELECT COALESCE(MAX(occurrence), 0) FROM transactions WHERE account_id = ? AND dedupe_key = ?",
            (account["id"], key),
        ).fetchone()[0]
        cursor = conn.execute(
            "INSERT INTO transactions (account_id, posted_on, amount_cents, description, merchant, dedupe_key,"
            " natural_key, occurrence, category_id, category_source, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account["id"],
                posted_on.isoformat(),
                amount_cents,
                description,
                merchant_key(description),
                key,
                natural,
                taken + 1,
                category["id"] if category is not None else None,
                "manual" if category is not None else None,
                clean_cell(note),
            ),
        )
    return cursor.lastrowid


def _check_entry(amount_cents: int, description: str) -> str:
    if amount_cents == 0:
        raise BooksError("an amount of zero does not move a balance")
    if abs(amount_cents) > MAX_CENTS:
        raise BooksError("that amount is too large to be a real row")
    description = clean_cell(description)
    if not description:
        raise BooksError("a transaction needs a description")
    return description


def edit_manual(
    conn: sqlite3.Connection,
    transaction_id: int,
    posted_on: Optional[date] = None,
    amount_cents: Optional[int] = None,
    description: Optional[str] = None,
) -> None:
    """Correct a row added by hand. A row from a bank file stays as the bank wrote it."""
    row = get_transaction(conn, transaction_id)
    if row["reconciliation_id"] is not None:
        raise BooksError(
            f"transaction {transaction_id} is part of finished reconciliation {row['reconciliation_id']}. "
            "Reopen that reconciliation first."
        )
    if row["import_id"] is not None or live_imports_holding(conn, transaction_id):
        raise BooksError(
            f"transaction {transaction_id} came from a bank file, so it stays as the bank wrote it. "
            "To correct it, delete it with 'txn delete' and add the right row with 'txn add'. Notes and categories can still change."
        )
    day = posted_on or date.fromisoformat(row["posted_on"])
    amount = row["amount_cents"] if amount_cents is None else amount_cents
    text = _check_entry(amount, row["description"] if description is None else description)
    natural = natural_key(day, amount, text)
    key = plain_key(natural)
    with transaction(conn):
        taken = conn.execute(
            "SELECT COALESCE(MAX(occurrence), 0) FROM transactions WHERE account_id = ? AND dedupe_key = ? AND id != ?",
            (row["account_id"], key, transaction_id),
        ).fetchone()[0]
        conn.execute(
            "UPDATE transactions SET posted_on = ?, amount_cents = ?, description = ?, merchant = ?,"
            " dedupe_key = ?, natural_key = ?, occurrence = ? WHERE id = ?",
            (day.isoformat(), amount, text, merchant_key(text), key, natural, taken + 1, transaction_id),
        )


def delete_transaction(conn: sqlite3.Connection, transaction_id: int) -> None:
    """Remove a row and remember it, so an overlapping export does not bring it back."""
    row = get_transaction(conn, transaction_id)
    if row["reconciliation_id"] is not None:
        raise BooksError(
            f"transaction {transaction_id} is part of finished reconciliation {row['reconciliation_id']}. "
            "Reopen that reconciliation first."
        )
    with transaction(conn):
        conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        if row["import_id"] is not None:
            conn.execute(
                "INSERT OR IGNORE INTO deleted_rows (account_id, dedupe_key, natural_key, occurrence) VALUES (?, ?, ?, ?)",
                (row["account_id"], row["dedupe_key"], row["natural_key"], row["occurrence"]),
            )


def _check_category(business_id: int, category: sqlite3.Row) -> None:
    if category["business_id"] != business_id:
        raise BooksError(f"category {category['code']} {category['name']} belongs to another business")
    require_usable(category)


def categorize(conn: sqlite3.Connection, transaction_ids: Iterable[int], category: sqlite3.Row) -> int:
    """Book rows to a category by hand. Rules will not change them afterward."""
    count = 0
    with transaction(conn):
        for transaction_id in transaction_ids:
            row = get_transaction(conn, transaction_id)
            _check_category(row["business_id"], category)
            conn.execute(
                "UPDATE transactions SET category_id = ?, category_source = 'manual', rule_id = NULL WHERE id = ?",
                (category["id"], transaction_id),
            )
            count += 1
    return count


def uncategorize(conn: sqlite3.Connection, transaction_ids: Iterable[int]) -> int:
    """Send rows back to the review queue."""
    count = 0
    with transaction(conn):
        for transaction_id in transaction_ids:
            get_transaction(conn, transaction_id)
            conn.execute(
                "UPDATE transactions SET category_id = NULL, category_source = NULL, rule_id = NULL WHERE id = ?",
                (transaction_id,),
            )
            count += 1
    return count


def set_note(conn: sqlite3.Connection, transaction_id: int, note: str) -> None:
    get_transaction(conn, transaction_id)
    # A note someone writes is theirs from now on: no rule's note replaces it.
    conn.execute("UPDATE transactions SET note = ?, note_from_rule = 0 WHERE id = ?", (clean_cell(note), transaction_id))


def balance_through(conn: sqlite3.Connection, account: sqlite3.Row, through: date) -> Optional[int]:
    """Balance at the end of a day, or None if the account has no opening balance to start from."""
    if account["opening_balance_cents"] is None:
        return None
    total = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions"
        " WHERE account_id = ? AND posted_on >= ? AND posted_on <= ?",
        (account["id"], account["opening_date"], through.isoformat()),
    ).fetchone()[0]
    return account["opening_balance_cents"] + total


def rows_before_opening(conn: sqlite3.Connection, account: sqlite3.Row) -> int:
    if account["opening_date"] is None:
        return 0
    return conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE account_id = ? AND posted_on < ?",
        (account["id"], account["opening_date"]),
    ).fetchone()[0]
