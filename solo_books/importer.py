"""Bringing a bank or card export into the books, safely and repeatably."""
from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from .csvmap import Mapping, ParsedFile, ParsedRow, decode, parse_text, read_bytes
from .db import transaction
from .errors import BooksError
from .layouts import describe_match
from .ledger import finished_reconciliation_on, natural_key, plain_key, reference_key
from .money import format_cents
from .ofx import OFX_MAPPING_JSON, looks_like_ofx, parse_ofx
from .rules import first_match, load_rules
from .text import merchant_key

_NEAR_DUPLICATE_DAYS = 3
# A bank can move a pending row's date by a few days and still call it the same row.
_SAME_ID_DAYS = 7
_MAX_ERRORS_SHOWN = 20
# A date this far ahead is a misread format, not a row posted early.
_FUTURE_DAYS = 7


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
    # One line for one row. There can be thousands, so these are shown after the warnings, and cut short.
    row_warnings: List[str] = field(default_factory=list)
    bad_rows: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)


class _DryRun(Exception):
    """Unwinds the transaction after a dry run has worked everything out."""


def account_mapping(account: sqlite3.Row, path: Optional[Path] = None) -> Mapping:
    if not account["mapping_json"]:
        layout = describe_match(path) if path else None
        hint = (
            f"This file looks like layout {layout}: 'account map \"{account['name']}\" --layout {layout}'."
            if layout
            else "Set one with 'account map', or get a starting point from 'mapping suggest FILE'."
        )
        raise BooksError(f"account {account['name']!r} has no column mapping yet. {hint}")
    return Mapping.from_json(account["mapping_json"])


def read_export(account: sqlite3.Row, path: Path, mapping: Optional[Mapping] = None) -> "Tuple[ParsedFile, str, str]":
    """Read a CSV through the account's mapping, or an OFX file, which needs none. Also gives its hash and how it was read."""
    data = read_bytes(path)
    sha = hashlib.sha256(data).hexdigest()
    if mapping is None and account["mapping_json"]:
        mapping = Mapping.from_json(account["mapping_json"])
    text = decode(data, path.name, mapping.encoding if mapping else None)
    if looks_like_ofx(text):
        return parse_ofx(text, path.name), sha, OFX_MAPPING_JSON
    mapping = mapping or account_mapping(account, path)
    return parse_text(text, mapping, path.name), sha, mapping.to_json()


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
    parsed, sha, how_read = read_export(account, path, mapping)
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

    earlier = conn.execute(
        "SELECT id, imported_at, mapping_json FROM imports WHERE account_id = ? AND file_sha256 = ? AND undone_at IS NULL"
        " ORDER BY id DESC",
        (account["id"], sha),
    ).fetchone()
    if earlier:
        result.warnings.append(f"This exact file was already imported (import {earlier['id']}, {earlier['imported_at']}).")

    try:
        with transaction(conn):
            _insert_rows(conn, account, path.name, sha, how_read, parsed.rows, result)
            _settle_conflicts(path.name, result, stop=not (dry_run or skip_bad))
            _check_remapped_file(earlier, how_read, result, stop=not dry_run)
            if dry_run:
                raise _DryRun
    except _DryRun:
        result.import_id = None

    _sign_warning(account, parsed.rows, result)
    _date_warnings(conn, account, parsed.rows, result)
    if parsed.data_rows == 0:
        result.warnings.append(f"{path.name} has a header but no rows. Check that the download covers the dates you chose.")
    return result


def _date_warnings(conn: sqlite3.Connection, account: sqlite3.Row, rows: List[ParsedRow], result: ImportResult) -> None:
    """Two ways a row can go in and then quietly fail to count the way someone expects."""
    latest_allowed = date.today() + timedelta(days=_FUTURE_DAYS)
    ahead = [row for row in rows if row.posted_on > latest_allowed]
    if ahead:
        result.warnings.append(
            f"{len(ahead)} row(s) are dated after today, up to {max(row.posted_on for row in ahead)}. "
            "If the months and days look swapped, fix date_format and undo this import."
        )
    if account["opening_date"]:
        early = [row for row in rows if row.posted_on.isoformat() < account["opening_date"]]
        if early:
            result.warnings.append(
                f"{len(early)} row(s) are dated before this account's opening date of {account['opening_date']}. "
                "They still count in profit and loss, but not in the account's balance. "
                "Move the opening date earlier with 'account opening' if they belong."
            )


class _Verdict(NamedTuple):
    kind: str  # new, duplicate, deleted or conflict
    key: str
    occurrence: int = 0
    detail: str = ""
    matched: Optional[int] = None


class _RowMatcher:
    """Decides whether each row of a file is already in the books.

    A row that carries a bank id is matched by that id, then by date, amount and text among rows
    that have no id yet (one added by hand, or imported before the mapping named an id column).
    A row with no id is matched by date, amount and text against every row. Each existing row can
    only be claimed once, so two identical purchases in a file need two rows in the books.
    """

    def __init__(self, conn: sqlite3.Connection, account_id: int) -> None:
        self.conn = conn
        self.account_id = account_id
        self.claimed: set = set()
        self.seen_ids: Counter = Counter()
        self._existing: Dict[str, List[sqlite3.Row]] = {}
        self._deleted: Dict[str, List[str]] = {}

    def _existing_for(self, natural: str) -> List[sqlite3.Row]:
        # Read once per key, before this file adds anything, so a file cannot match its own rows.
        if natural not in self._existing:
            self._existing[natural] = self.conn.execute(
                "SELECT id, dedupe_key FROM transactions WHERE account_id = ? AND natural_key = ? ORDER BY id",
                (self.account_id, natural),
            ).fetchall()
        return self._existing[natural]

    def _deleted_for(self, natural: str) -> List[str]:
        if natural not in self._deleted:
            self._deleted[natural] = [
                row["dedupe_key"]
                for row in self.conn.execute(
                    "SELECT dedupe_key FROM deleted_rows WHERE account_id = ? AND natural_key = ?",
                    (self.account_id, natural),
                )
            ]
        return self._deleted[natural]

    def _claim(self, natural: str, only_without_id: bool) -> Optional[int]:
        for row in self._existing_for(natural):
            if row["id"] in self.claimed:
                continue
            if only_without_id and not row["dedupe_key"].startswith("row:"):
                continue
            self.claimed.add(row["id"])
            return row["id"]
        return None

    def classify(self, row: ParsedRow) -> _Verdict:
        natural = natural_key(row.posted_on, row.amount_cents, row.description)
        if not row.reference:
            key = plain_key(natural)
            matched = self._claim(natural, only_without_id=False)
            if matched is not None:
                return _Verdict("duplicate", key, matched=matched)
            if self._deleted_for(natural):
                self._deleted_for(natural).pop()
                return _Verdict("deleted", key)
            taken = self.conn.execute(
                "SELECT COALESCE(MAX(occurrence), 0) FROM transactions WHERE account_id = ? AND dedupe_key = ?",
                (self.account_id, key),
            ).fetchone()[0]
            return _Verdict("new", key, taken + 1)

        key = reference_key(row.reference)
        self.seen_ids[key] += 1
        occurrence = self.seen_ids[key]
        if self.conn.execute(
            "SELECT 1 FROM deleted_rows WHERE account_id = ? AND dedupe_key = ? AND occurrence = ?",
            (self.account_id, key, occurrence),
        ).fetchone():
            return _Verdict("deleted", key, occurrence)
        holder = self.conn.execute(
            "SELECT id, posted_on, amount_cents, description FROM transactions"
            " WHERE account_id = ? AND dedupe_key = ? AND occurrence = ?",
            (self.account_id, key, occurrence),
        ).fetchone()
        if holder is not None:
            self.claimed.add(holder["id"])
            days_apart = abs((date.fromisoformat(holder["posted_on"]) - row.posted_on).days)
            if holder["amount_cents"] == row.amount_cents and days_apart <= _SAME_ID_DAYS:
                return _Verdict("duplicate", key, occurrence, matched=holder["id"])
            return _Verdict(
                "conflict",
                key,
                occurrence,
                f"line {row.line}: id {row.reference!r} is already on transaction {holder['id']} "
                f"({holder['posted_on']}, {format_cents(holder['amount_cents'])}, {holder['description']!r}), "
                f"but this row reads {row.posted_on}, {format_cents(row.amount_cents)}, {row.description!r}",
            )
        without_id = self._claim(natural, only_without_id=True)
        if without_id is not None:
            # The row is already here; from now on the bank's id identifies it.
            self.conn.execute(
                "UPDATE transactions SET dedupe_key = ?, occurrence = ?, reference = ? WHERE id = ?",
                (key, occurrence, row.reference, without_id),
            )
            return _Verdict("duplicate", key, occurrence, matched=without_id)
        deleted = self._deleted_for(natural)
        for index, deleted_key in enumerate(deleted):
            if deleted_key.startswith("row:"):
                del deleted[index]
                return _Verdict("deleted", key, occurrence)
        return _Verdict("new", key, occurrence)


def _insert_rows(
    conn: sqlite3.Connection,
    account: sqlite3.Row,
    file_name: str,
    sha: str,
    mapping_json: str,
    rows: List[ParsedRow],
    result: ImportResult,
) -> None:
    cursor = conn.execute(
        "INSERT INTO imports (account_id, file_name, file_sha256, rows_read, rows_new, rows_duplicate, rows_skipped,"
        " mapping_json) VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
        (account["id"], file_name, sha, result.rows_read, result.rows_skipped, mapping_json),
    )
    import_id = cursor.lastrowid
    result.import_id = import_id
    rules = load_rules(conn, account["business_id"])
    matcher = _RowMatcher(conn, account["id"])

    # Two identical rows on one day in one file are two real purchases: each needs its own row
    # in the books, so a later export holding the same two matches them and adds nothing.
    repeats: Counter = Counter()
    for row in rows:
        natural = natural_key(row.posted_on, row.amount_cents, row.description)
        if not row.reference:
            repeats[natural] += 1
        verdict = matcher.classify(row)
        if verdict.kind == "duplicate":
            _record_held(conn, import_id, verdict.matched)
            result.rows_duplicate += 1
            continue
        if verdict.kind == "deleted":
            result.rows_deleted_before += 1
            continue
        if verdict.kind == "conflict":
            result.conflicts.append(verdict.detail)
            continue

        if row.reference and verdict.occurrence > 1:
            result.row_warnings.append(
                f"Line {row.line}: bank id {row.reference!r} appears more than once in this file. Each was kept as its own "
                f"row. If that doubled something, remove the extra with 'txn delete'."
            )
        merchant = merchant_key(row.description)
        rule = first_match(rules, account["id"], row.description, row.memo, merchant, row.amount_cents)
        _warn_near_duplicate(conn, account, row, verdict.key, result)
        inserted = conn.execute(
            "INSERT INTO transactions (account_id, posted_on, amount_cents, description, memo, reference, merchant,"
            " dedupe_key, natural_key, occurrence, import_id, category_id, category_source, rule_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account["id"],
                row.posted_on.isoformat(),
                row.amount_cents,
                row.description,
                row.memo,
                row.reference,
                merchant,
                verdict.key,
                natural,
                verdict.occurrence,
                import_id,
                rule.category_id if rule else None,
                "rule" if rule else None,
                rule.id if rule else None,
            ),
        )
        _record_held(conn, import_id, inserted.lastrowid)
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
    total = sum(count for count in repeats.values() if count > 1)
    if total:
        result.warnings.append(
            f"{total} rows in this file repeat another row on the same day with the same amount and text. "
            "They were kept as separate purchases. If one is a mistake, remove it with 'txn delete'."
        )


def _record_held(conn: sqlite3.Connection, import_id: int, transaction_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO import_rows (import_id, transaction_id) VALUES (?, ?)", (import_id, transaction_id)
    )


def _settle_conflicts(file_name: str, result: ImportResult, stop: bool) -> None:
    """A reused id must not swallow a different row, and must not double a corrected one. A person decides."""
    if not result.conflicts:
        return
    if stop:
        shown = "\n  ".join(result.conflicts[:_MAX_ERRORS_SHOWN])
        raise BooksError(
            f"{file_name}: {len(result.conflicts)} row(s) reuse a bank id with a different date or amount. "
            f"Nothing was imported.\n  {shown}\n"
            "If this column is not a unique id (a row number, say), drop it with 'account map NAME --no-reference-col'. "
            "If the bank really changed the row, pass --skip-bad and correct it by hand."
        )
    result.bad_rows.extend(result.conflicts)
    result.rows_bad += len(result.conflicts)


def _same_reading(left: str, right: str) -> bool:
    """Two records of how a file was read mean the same thing even if a newer version wrote more fields."""
    if left == right or OFX_MAPPING_JSON in (left, right):
        return left == right
    return Mapping.from_json(left).to_json() == Mapping.from_json(right).to_json()


def _check_remapped_file(earlier: Optional[sqlite3.Row], mapping_json: str, result: ImportResult, stop: bool) -> None:
    """The same file read a new way would stack new rows on the old ones, such as both signs of each amount."""
    if earlier is None or not result.rows_new or _same_reading(earlier["mapping_json"], mapping_json):
        return
    message = (
        f"This exact file is already in the books as import {earlier['id']}, read with a different mapping, "
        f"so {result.rows_new} row(s) would go in on top of the old ones. "
        f"Take the old ones out first with 'imports undo {earlier['id']}'."
    )
    if stop:
        raise BooksError(message)
    result.warnings.append(message)


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
        result.row_warnings.append(
            f"Possible duplicate: {row.posted_on} {format_cents(row.amount_cents)} {row.description!r} is new, but "
            f"transaction {match['id']} ({match['posted_on']}, {match['description']!r}) looks the same."
        )


def _warn_inside_finished(conn: sqlite3.Connection, account: sqlite3.Row, row: ParsedRow, result: ImportResult) -> None:
    hit = finished_reconciliation_on(conn, account["id"], row.posted_on)
    if hit:
        result.row_warnings.append(
            f"New row {row.posted_on} {format_cents(row.amount_cents)} {row.description!r} falls inside "
            f"finished reconciliation {hit}, so that period no longer ties. Check it with 'reconcile show {hit}'."
        )


def _sign_warning(account: sqlite3.Row, rows: List[ParsedRow], result: ImportResult) -> None:
    """The most common mapping mistake is a sign that runs backward. Catch it on the first import."""
    if len(rows) < 3:
        return
    money_in = sum(1 for row in rows if row.amount_cents > 0)
    if account["kind"] == "credit_card" and money_in * 2 > len(rows):
        result.warnings.append(
            f"{money_in} of {len(rows)} rows read as money in, which is unusual for a card. "
            "If charges are positive in this export, set invert_amount to true in the mapping. "
            "If this file is already imported, take it out first with 'imports undo ID'."
        )
    if account["kind"] in ("checking", "savings") and money_in == len(rows):
        result.warnings.append(
            "Every row reads as money in. If withdrawals are positive in this export, fix the mapping. "
            "If this file is already imported, take it out first with 'imports undo ID'."
        )


def list_imports(conn: sqlite3.Connection, account_id: Optional[int] = None) -> List[sqlite3.Row]:
    sql = (
        "SELECT i.*, a.name AS account_name FROM imports i JOIN accounts a ON a.id = i.account_id"
    )
    if account_id is None:
        return conn.execute(sql + " ORDER BY i.id").fetchall()
    return conn.execute(sql + " WHERE i.account_id = ? ORDER BY i.id", (account_id,)).fetchall()


def get_import(conn: sqlite3.Connection, import_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT i.*, a.name AS account_name FROM imports i JOIN accounts a ON a.id = i.account_id WHERE i.id = ?",
        (import_id,),
    ).fetchone()
    if row is None:
        raise BooksError(f"no import {import_id}")
    return row


def undo_import(conn: sqlite3.Connection, import_id: int, force: bool = False) -> Dict:
    """Take back what only this file put in the books. A row another live import also held stays."""
    row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
    if row is None:
        raise BooksError(f"no import {import_id}")
    if row["undone_at"]:
        raise BooksError(f"import {import_id} was already undone")
    owned = conn.execute(
        "SELECT t.id, t.category_source, t.reconciliation_id,"
        " (SELECT MIN(h.import_id) FROM import_rows h WHERE h.transaction_id = t.id AND h.import_id != ?) AS also_in"
        " FROM transactions t WHERE t.import_id = ?",
        (import_id, import_id),
    ).fetchall()
    kept = [item for item in owned if item["also_in"] is not None]
    going = [item for item in owned if item["also_in"] is None]
    reconciled = sum(1 for item in going if item["reconciliation_id"] is not None)
    if reconciled:
        raise BooksError(f"{reconciled} row(s) from import {import_id} are in a finished reconciliation. Reopen it first.")
    manual = sum(1 for item in going if item["category_source"] == "manual")
    if manual and not force:
        raise BooksError(
            f"{manual} row(s) from import {import_id} were categorized by hand and that work would be lost. "
            "Pass --force to undo anyway."
        )
    with transaction(conn):
        for item in kept:
            conn.execute("UPDATE transactions SET import_id = ? WHERE id = ?", (item["also_in"], item["id"]))
        for item in going:
            conn.execute("DELETE FROM transactions WHERE id = ?", (item["id"],))
        conn.execute("DELETE FROM import_rows WHERE import_id = ?", (import_id,))
        conn.execute("UPDATE imports SET undone_at = datetime('now') WHERE id = ?", (import_id,))
    return {
        "removed": len(going),
        "kept": len(kept),
        "kept_in": sorted({item["also_in"] for item in kept}),
        "manual_lost": manual,
    }
