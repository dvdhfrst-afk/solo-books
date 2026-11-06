"""Bringing a bank or card export into the books, safely and repeatably."""
from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .csvmap import Mapping, ParsedRow, parse_export
from .db import transaction
from .errors import BooksError
from .ledger import dedupe_key
from .money import format_cents
from .rules import first_match, load_rules
from .text import merchant_key

_NEAR_DUPLICATE_DAYS = 3
_MAX_ERRORS_SHOWN = 20


@dataclass
class ImportResult:
    account: str
    file_name: str
    import_id: Optional[int] = None
    dry_run: bool = False
    rows_read: int = 0
    rows_new: int = 0
    rows_duplicate: int = 0
    rows_deleted_before: int = 0
    rows_skipped: int = 0
    rows_bad: int = 0
    auto_categorized: int = 0
    waiting_for_review: int = 0
    earliest: Optional[date] = None
    latest: Optional[date] = None
    samples: List[ParsedRow] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    bad_rows: List[str] = field(default_factory=list)


class _DryRun(Exception):
    """Unwinds the transaction after a dry run has worked everything out."""


def account_mapping(account: sqlite3.Row) -> Mapping:
    if not account["mapping_json"]:
        raise BooksError(
            f"account {account['name']!r} has no column mapping yet. "
            "Set one with 'account map', or get a starting point from 'mapping suggest FILE'."
        )
    return Mapping.from_json(account["mapping_json"])


def import_export(
    conn: sqlite3.Connection,
    account: sqlite3.Row,
    path: Path,
    dry_run: bool = False,
    skip_bad: bool = False,
    mapping: Optional[Mapping] = None,
) -> ImportResult:
    """Import one file. Every row goes in, or none do, unless skip_bad is asked for."""
    path = Path(path)
    mapping = mapping or account_mapping(account)
    parsed = parse_export(path, mapping)
    if parsed.errors and not skip_bad and not dry_run:
        shown = "\n  ".join(parsed.errors[:_MAX_ERRORS_SHOWN])
        more = len(parsed.errors) - _MAX_ERRORS_SHOWN
        extra = f"\n  ...and {more} more" if more > 0 else ""
        raise BooksError(
            f"{path.name}: {len(parsed.errors)} row(s) could not be read. Nothing was imported.\n  {shown}{extra}\n"
            "Fix the mapping or the file, or pass --skip-bad to import the rest."
        )

    result = ImportResult(account=account["name"], file_name=path.name, dry_run=dry_run)
    result.rows_read = parsed.data_rows
    result.rows_skipped = parsed.skipped
    result.rows_bad = len(parsed.errors)
    result.bad_rows = parsed.errors
    result.samples = parsed.rows[:5]
    if parsed.rows:
        days = [row.posted_on for row in parsed.rows]
        result.earliest, result.latest = min(days), max(days)

    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    earlier = conn.execute(
        "SELECT id, imported_at FROM imports WHERE account_id = ? AND file_sha256 = ? AND undone_at IS NULL",
        (account["id"], sha),
    ).fetchone()
    if earlier:
        result.warnings.append(f"This exact file was already imported (import {earlier['id']}, {earlier['imported_at']}).")

    try:
        with transaction(conn):
            _insert_rows(conn, account, path.name, sha, parsed.rows, result)
            if dry_run:
                raise _DryRun
    except _DryRun:
        result.import_id = None

    _sign_warning(account, parsed.rows, result)
    return result


def _insert_rows(
    conn: sqlite3.Connection,
    account: sqlite3.Row,
    file_name: str,
    sha: str,
    rows: List[ParsedRow],
    result: ImportResult,
) -> None:
    cursor = conn.execute(
        "INSERT INTO imports (account_id, file_name, file_sha256, rows_read, rows_new, rows_duplicate, rows_skipped)"
        " VALUES (?, ?, ?, ?, 0, 0, ?)",
        (account["id"], file_name, sha, result.rows_read, result.rows_skipped),
    )
    import_id = cursor.lastrowid
    result.import_id = import_id
    rules = load_rules(conn, account["business_id"])

    # Two identical rows on one day in one file are two real purchases. They are told apart by
    # counting, so a later export holding the same two rows matches them and adds nothing.
    seen: Counter = Counter()
    repeated: Counter = Counter()
    for row in rows:
        key = dedupe_key(row.posted_on, row.amount_cents, row.description, row.reference)
        seen[key] += 1
        occurrence = seen[key]
        if occurrence > 1:
            repeated[key] = occurrence
        if conn.execute(
            "SELECT 1 FROM transactions WHERE account_id = ? AND dedupe_key = ? AND occurrence = ?",
            (account["id"], key, occurrence),
        ).fetchone():
            result.rows_duplicate += 1
            continue
        if conn.execute(
            "SELECT 1 FROM deleted_rows WHERE account_id = ? AND dedupe_key = ? AND occurrence = ?",
            (account["id"], key, occurrence),
        ).fetchone():
            result.rows_deleted_before += 1
            continue

        merchant = merchant_key(row.description)
        rule = first_match(rules, account["id"], row.description, row.memo, merchant, row.amount_cents)
        _warn_near_duplicate(conn, account, row, key, result)
        conn.execute(
            "INSERT INTO transactions (account_id, posted_on, amount_cents, description, memo, reference, merchant,"
            " dedupe_key, occurrence, import_id, category_id, category_source, rule_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account["id"],
                row.posted_on.isoformat(),
                row.amount_cents,
                row.description,
                row.memo,
                row.reference,
                merchant,
                key,
                occurrence,
                import_id,
                rule.category_id if rule else None,
                "rule" if rule else None,
                rule.id if rule else None,
            ),
        )
        result.rows_new += 1
        if rule:
            result.auto_categorized += 1
        else:
            result.waiting_for_review += 1
        _warn_inside_finished(conn, account, row, result)

    conn.execute(
        "UPDATE imports SET rows_new = ?, rows_duplicate = ? WHERE id = ?",
        (result.rows_new, result.rows_duplicate, import_id),
    )
    if repeated:
        total = sum(repeated.values())
        result.warnings.append(
            f"{total} rows in this file repeat another row on the same day with the same amount and text. "
            "They were kept as separate purchases. If one is a mistake, remove it with 'txn delete'."
        )


def _warn_near_duplicate(
    conn: sqlite3.Connection, account: sqlite3.Row, row: ParsedRow, key: str, result: ImportResult
) -> None:
    """A row that changed its wording between exports looks new. Flag it instead of guessing."""
    low = (row.posted_on - timedelta(days=_NEAR_DUPLICATE_DAYS)).isoformat()
    high = (row.posted_on + timedelta(days=_NEAR_DUPLICATE_DAYS)).isoformat()
    match = conn.execute(
        "SELECT id, posted_on, description FROM transactions"
        " WHERE account_id = ? AND amount_cents = ? AND posted_on BETWEEN ? AND ? AND dedupe_key != ?"
        " AND (import_id IS NULL OR import_id != ?) AND merchant = ?",
        (account["id"], row.amount_cents, low, high, key, result.import_id, merchant_key(row.description)),
    ).fetchone()
    if match:
        result.warnings.append(
            f"Possible duplicate: {row.posted_on} {format_cents(row.amount_cents)} {row.description!r} is new, but "
            f"transaction {match['id']} ({match['posted_on']}, {match['description']!r}) looks the same."
        )


def _warn_inside_finished(conn: sqlite3.Connection, account: sqlite3.Row, row: ParsedRow, result: ImportResult) -> None:
    hit = conn.execute(
        "SELECT id FROM reconciliations WHERE account_id = ? AND status = 'finished'"
        " AND period_start <= ? AND period_end >= ?",
        (account["id"], row.posted_on.isoformat(), row.posted_on.isoformat()),
    ).fetchone()
    if hit:
        result.warnings.append(
            f"New row {row.posted_on} {format_cents(row.amount_cents)} {row.description!r} falls inside "
            f"finished reconciliation {hit['id']}, so that period no longer ties. Check it with 'reconcile show {hit['id']}'."
        )


def _sign_warning(account: sqlite3.Row, rows: List[ParsedRow], result: ImportResult) -> None:
    """The most common mapping mistake is a sign that runs backward. Catch it on the first import."""
    if len(rows) < 3:
        return
    money_in = sum(1 for row in rows if row.amount_cents > 0)
    if account["kind"] == "credit_card" and money_in * 2 > len(rows):
        result.warnings.append(
            f"{money_in} of {len(rows)} rows read as money in, which is unusual for a card. "
            "If charges are positive in this export, set invert_amount to true in the mapping."
        )
    if account["kind"] in ("checking", "savings") and money_in == len(rows):
        result.warnings.append(
            "Every row reads as money in. If withdrawals are positive in this export, fix the mapping."
        )


def list_imports(conn: sqlite3.Connection, account_id: Optional[int] = None) -> List[sqlite3.Row]:
    sql = (
        "SELECT i.*, a.name AS account_name FROM imports i JOIN accounts a ON a.id = i.account_id"
    )
    if account_id is None:
        return conn.execute(sql + " ORDER BY i.id").fetchall()
    return conn.execute(sql + " WHERE i.account_id = ? ORDER BY i.id", (account_id,)).fetchall()


def undo_import(conn: sqlite3.Connection, import_id: int, force: bool = False) -> Dict[str, int]:
    """Take a whole import back out. Importing the same file again later works normally."""
    row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
    if row is None:
        raise BooksError(f"no import {import_id}")
    if row["undone_at"]:
        raise BooksError(f"import {import_id} was already undone")
    reconciled = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE import_id = ? AND reconciliation_id IS NOT NULL", (import_id,)
    ).fetchone()[0]
    if reconciled:
        raise BooksError(f"{reconciled} row(s) from import {import_id} are in a finished reconciliation. Reopen it first.")
    manual = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE import_id = ? AND category_source = 'manual'", (import_id,)
    ).fetchone()[0]
    if manual and not force:
        raise BooksError(
            f"{manual} row(s) from import {import_id} were categorized by hand and that work would be lost. "
            "Pass --force to undo anyway."
        )
    with transaction(conn):
        removed = conn.execute("DELETE FROM transactions WHERE import_id = ?", (import_id,)).rowcount
        conn.execute("UPDATE imports SET undone_at = datetime('now') WHERE id = ?", (import_id,))
    return {"removed": removed, "manual_lost": manual}
