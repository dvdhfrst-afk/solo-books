"""The record of every change to the books, and taking back the changes that can safely be taken back.

Each change writes one entry in the same database transaction as the change itself, so the books and their
history never disagree. An entry says when, which command made it, what kind of change it was and, for each bank
row it touched, what mattered about that row before and after. Values are copied rather than linked, so an entry
still reads correctly after the row, rule or name it describes has gone.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .db import transaction
from .errors import BooksError
from .money import format_cents
from .periods import Period

# Every kind of change, with the words 'history list --kind' help shows for it.
KINDS: Dict[str, str] = {
    "business-add": "a business added",
    "business-rename": "a business renamed",
    "business-delete": "a business deleted",
    "account-add": "an account added",
    "account-rename": "an account renamed",
    "account-delete": "an account deleted, with any rows it held",
    "account-map": "an account's column mapping set",
    "account-opening": "an account's opening balance set",
    "chart-add": "a category added",
    "chart-rename": "a category renamed",
    "chart-map": "a category pointed at a form line",
    "chart-retire": "a category retired",
    "inventory-set": "a year's inventory counts saved",
    "import": "a file imported",
    "import-undo": "an import taken back out",
    "rule-add": "a rule added",
    "rule-delete": "a rule deleted",
    "rule-apply": "rules run over rows",
    "rule-import": "rules added from a file",
    "rule-hold-back": "rows a rule must never book",
    "categorize": "rows booked or sent back to the queue by hand",
    "note": "a row's note changed",
    "txn-add": "a row added by hand",
    "txn-edit": "a row added by hand corrected",
    "txn-delete": "a row deleted",
    "reconcile-run": "a statement's figures recorded",
    "reconcile-finish": "a statement period locked",
    "reconcile-reopen": "a statement period unlocked",
    "reconcile-forget": "an open reconciliation removed",
    "tag-add": "rows tagged",
    "tag-remove": "a tag taken off rows",
    "tag-rename": "a tag renamed",
    "tag-delete": "a tag deleted",
    "payee-rename": "a payee renamed",
    "payee-merge": "two merchant spellings made one payee",
    "payee-unmerge": "a merchant spelling made its own payee again",
    "payee-set": "a payee's contractor flag set",
    "budget-set": "a budget set",
    "budget-delete": "a budget deleted",
    "budget-copy": "a year's budgets copied to another year",
    "revert": "an earlier change taken back",
}

REVERTIBLE = ("categorize", "note", "chart-rename", "chart-map", "rule-add", "rule-delete")

_source = ""


def set_source(name: str) -> None:
    """Name the command whose changes are being recorded, such as 'txn note' or 'web'."""
    global _source
    _source = name


def current_source() -> str:
    return _source


def _dump(value: Optional[dict]) -> Optional[str]:
    return None if value is None else json.dumps(value, sort_keys=True)


def _load(text: Optional[str]) -> Optional[dict]:
    return None if text is None else json.loads(text)


RowChange = Tuple[int, Optional[dict], Optional[dict]]


def record(
    conn: sqlite3.Connection,
    kind: str,
    summary: str,
    business_id: Optional[int] = None,
    detail: Optional[dict] = None,
    rows: Iterable[RowChange] = (),
) -> int:
    """Write one entry. The caller holds the transaction that makes the change, so both land or neither does."""
    if kind not in KINDS:
        raise AssertionError(f"unknown history kind {kind!r}")
    if not conn.in_transaction:
        raise AssertionError("history must be written in the same transaction as the change it records")
    cursor = conn.execute(
        "INSERT INTO history (source, kind, business_id, summary, detail_json) VALUES (?, ?, ?, ?, ?)",
        (_source, kind, business_id, summary, json.dumps(detail or {}, sort_keys=True)),
    )
    entry_id = cursor.lastrowid
    conn.executemany(
        "INSERT INTO history_rows (history_id, transaction_id, before_json, after_json) VALUES (?, ?, ?, ?)",
        [(entry_id, transaction_id, _dump(before), _dump(after)) for transaction_id, before, after in rows],
    )
    return entry_id


# What is copied about a row

def category_labels(conn: sqlite3.Connection, business_id: int) -> Dict[int, str]:
    return {
        row["id"]: f"{row['code']} {row['name']}"
        for row in conn.execute("SELECT id, code, name FROM categories WHERE business_id = ?", (business_id,))
    }


def booking(category_id: Optional[int], label: Optional[str], source: Optional[str], rule_id: Optional[int]) -> dict:
    """How a row is booked: its category, whether by hand or by a rule, and which rule."""
    return {"category_id": category_id, "category": label, "by": source, "rule_id": rule_id}


def booking_of(row: sqlite3.Row) -> dict:
    """The booking of a row read with ledger's columns, which carry the category's code and name."""
    label = f"{row['category_code']} {row['category_name']}" if row["category_id"] is not None else None
    return booking(row["category_id"], label, row["category_source"], row["rule_id"])


def snapshot(row: sqlite3.Row) -> dict:
    """Everything a person would want to see again about a row that is added, changed or removed."""
    state = {
        "date": row["posted_on"],
        "amount_cents": row["amount_cents"],
        "description": row["description"],
        "account": row["account_name"],
    }
    state.update(booking_of(row))
    if row["note"]:
        state["note"] = row["note"]
    return state


# Reading the record

def check_kind(text: str) -> str:
    """A kind such as rule-add, or the first word of several, such as rule."""
    wanted = text.strip().lower()
    groups = {kind.split("-")[0] for kind in KINDS}
    if wanted in KINDS or wanted in groups:
        return wanted
    raise BooksError(f"no kind of change called {text!r}. Kinds: {', '.join(sorted(KINDS))}, or a first word such as rule.")


_LIST = (
    "SELECT h.*, b.name AS business_name,"
    " (SELECT COUNT(*) FROM history_rows r WHERE r.history_id = h.id) AS row_count"
    " FROM history h LEFT JOIN businesses b ON b.id = h.business_id"
)


def list_entries(
    conn: sqlite3.Connection,
    period: Optional[Period] = None,
    kind: Optional[str] = None,
    transaction_id: Optional[int] = None,
    business_id: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[sqlite3.Row]:
    """Newest first, because the last few changes are the ones a person usually wants."""
    where, params = [], []  # type: ignore[var-annotated]
    if period is not None:
        where.append("date(h.changed_at) BETWEEN ? AND ?")
        params.extend([period.start.isoformat(), period.end.isoformat()])
    if kind is not None:
        kind = check_kind(kind)
        where.append("(h.kind = ? OR h.kind LIKE ?)")
        params.extend([kind, f"{kind}-%"])
    if transaction_id is not None:
        where.append("EXISTS (SELECT 1 FROM history_rows r WHERE r.history_id = h.id AND r.transaction_id = ?)")
        params.append(transaction_id)
    if business_id is not None:
        where.append("h.business_id = ?")
        params.append(business_id)
    sql = _LIST + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY h.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row:
    row = conn.execute(_LIST + " WHERE h.id = ?", (entry_id,)).fetchone()
    if row is None:
        raise BooksError(f"no change {entry_id} in the history. See 'history list'.")
    return row


def entry_detail(entry: sqlite3.Row) -> dict:
    return json.loads(entry["detail_json"])


def entry_rows(conn: sqlite3.Connection, entry_id: int) -> List[Tuple[int, Optional[dict], Optional[dict]]]:
    return [
        (row["transaction_id"], _load(row["before_json"]), _load(row["after_json"]))
        for row in conn.execute(
            "SELECT transaction_id, before_json, after_json FROM history_rows WHERE history_id = ? ORDER BY rowid",
            (entry_id,),
        )
    ]


_BY_WORDS = {"manual": "by hand", "rule": "by a rule"}


def describe(state: Optional[dict]) -> str:
    """One line for a row's state as an entry copied it."""
    if state is None:
        return "-"
    parts: List[str] = []
    if "date" in state:
        parts.append(f"{state['date']} {format_cents(state['amount_cents'])} {state['description']!r} in {state['account']}")
    if "category_id" in state:
        if state["category_id"] is None:
            parts.append("in the queue")
        else:
            by = _BY_WORDS.get(state.get("by") or "", "")
            rule = f" {state['rule_id']}" if state.get("by") == "rule" and state.get("rule_id") else ""
            parts.append(f"{state['category']} ({by}{rule})" if by else str(state["category"]))
    if "note" in state:
        parts.append(f"note {state['note']!r}" if state["note"] else "no note")
    if "reconciliation_id" in state:
        recon = state["reconciliation_id"]
        parts.append(f"locked by reconciliation {recon}" if recon else "not locked")
    if "tag" in state:
        parts.append(f"tagged {state['tag']}" if state.get("tagged") else f"not tagged {state['tag']}")
    if "held_back_from_rule" in state:
        parts.append(f"never booked by rule {state['held_back_from_rule']}")
    return ", ".join(parts) or "-"


# Taking a change back

def revert(conn: sqlite3.Connection, entry_id: int) -> Tuple[int, str]:
    """Undo one recorded change, if that is safe. Returns the new entry's id and what was done."""
    entry = get_entry(conn, entry_id)
    if entry["reverted_by"] is not None:
        raise BooksError(f"change {entry_id} was already taken back by change {entry['reverted_by']}.")
    kind = entry["kind"]
    if kind not in REVERTIBLE:
        raise BooksError(
            f"change {entry_id} ({KINDS.get(kind, kind)}) cannot be taken back from the history. Only these can: "
            f"{', '.join(REVERTIBLE)}. Make the opposite change with its own command instead."
        )
    handler = {
        "categorize": _revert_categorize,
        "note": _revert_note,
        "chart-rename": _revert_chart_rename,
        "chart-map": _revert_chart_map,
        "rule-add": _revert_rule_add,
        "rule-delete": _revert_rule_delete,
    }[kind]
    with transaction(conn):
        summary, rows = handler(conn, entry, entry_detail(entry))
        new_id = record(
            conn,
            "revert",
            f"Took back change {entry_id}: {summary}",
            entry["business_id"],
            {"reverts": entry_id, "reverted_kind": kind},
            rows,
        )
        conn.execute("UPDATE history SET reverted_by = ? WHERE id = ?", (new_id, entry_id))
    return new_id, summary


def _current_booking(conn: sqlite3.Connection, transaction_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT t.id, t.category_id, t.category_source, t.rule_id, t.note, a.business_id"
        " FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = ?",
        (transaction_id,),
    ).fetchone()


def _changed_since(entry_id: int, transaction_id: int, what: str) -> BooksError:
    return BooksError(
        f"transaction {transaction_id} {what} since change {entry_id}, so taking it back would undo later work. "
        f"See 'history list --row {transaction_id}'. Nothing was changed."
    )


def _revert_categorize(conn, entry, detail) -> Tuple[str, List[RowChange]]:
    changes: List[RowChange] = []
    for transaction_id, before, after in entry_rows(conn, entry["id"]):
        current = _current_booking(conn, transaction_id)
        if current is None:
            raise _changed_since(entry["id"], transaction_id, "was deleted")
        if (current["category_id"], current["category_source"]) != (after["category_id"], after["by"]):
            raise _changed_since(entry["id"], transaction_id, "was booked again")
        if before["category_id"] is not None and conn.execute(
            "SELECT 1 FROM categories WHERE id = ?", (before["category_id"],)
        ).fetchone() is None:
            raise BooksError(f"the category transaction {transaction_id} had before change {entry['id']} is gone.")
        rule_id = before["rule_id"]
        if rule_id is not None and conn.execute("SELECT 1 FROM rules WHERE id = ?", (rule_id,)).fetchone() is None:
            rule_id = None
        conn.execute(
            "UPDATE transactions SET category_id = ?, category_source = ?, rule_id = ? WHERE id = ?",
            (before["category_id"], before["by"], rule_id, transaction_id),
        )
        now = booking(current["category_id"], after["category"], current["category_source"], current["rule_id"])
        changes.append((transaction_id, now, booking(before["category_id"], before["category"], before["by"], rule_id)))
    return f"{len(changes)} row(s) booked as they were before.", changes


def _revert_note(conn, entry, detail) -> Tuple[str, List[RowChange]]:
    changes: List[RowChange] = []
    for transaction_id, before, after in entry_rows(conn, entry["id"]):
        current = _current_booking(conn, transaction_id)
        if current is None:
            raise _changed_since(entry["id"], transaction_id, "was deleted")
        if current["note"] != after["note"]:
            raise _changed_since(entry["id"], transaction_id, "got another note")
        conn.execute("UPDATE transactions SET note = ? WHERE id = ?", (before["note"], transaction_id))
        changes.append((transaction_id, {"note": current["note"]}, {"note": before["note"]}))
    return f"the note on transaction {', '.join(str(row[0]) for row in changes)} is back as it was.", changes


def _category(conn, detail) -> sqlite3.Row:
    category = conn.execute("SELECT * FROM categories WHERE id = ?", (detail["category_id"],)).fetchone()
    if category is None:
        raise BooksError(f"category {detail['code']} is gone, with the business it belonged to.")
    return category


def _revert_chart_rename(conn, entry, detail) -> Tuple[str, List[RowChange]]:
    category = _category(conn, detail)
    if category["name"] != detail["after"]:
        raise BooksError(
            f"category {category['code']} was renamed again since change {entry['id']} (it is now {category['name']!r}). "
            "Nothing was changed."
        )
    clash = conn.execute(
        "SELECT code FROM categories WHERE business_id = ? AND name = ? AND id != ?",
        (category["business_id"], detail["before"], category["id"]),
    ).fetchone()
    if clash:
        raise BooksError(f"the name {detail['before']!r} now belongs to category {clash['code']}. Nothing was changed.")
    conn.execute("UPDATE categories SET name = ? WHERE id = ?", (detail["before"], category["id"]))
    return f"category {category['code']} is called {detail['before']!r} again.", []


def _revert_chart_map(conn, entry, detail) -> Tuple[str, List[RowChange]]:
    category = _category(conn, detail)
    if category["schedule_c_key"] != detail["after"]:
        raise BooksError(f"category {category['code']} was pointed at another line since change {entry['id']}. Nothing was changed.")
    conn.execute("UPDATE categories SET schedule_c_key = ? WHERE id = ?", (detail["before"], category["id"]))
    where = f"line key {detail['before']}" if detail["before"] else "off the form"
    return f"category {category['code']} is back on {where}.", []


RULE_FIELDS = (
    "id", "business_id", "account_id", "priority", "match_type", "pattern", "direction", "min_cents", "max_cents",
    "category_id", "created_at",
)


def rule_detail(conn: sqlite3.Connection, rule: sqlite3.Row) -> dict:
    detail = {name: rule[name] for name in RULE_FIELDS}
    category = conn.execute("SELECT code, name FROM categories WHERE id = ?", (rule["category_id"],)).fetchone()
    detail["category"] = f"{category['code']} {category['name']}" if category else None
    return detail


def _revert_rule_add(conn, entry, detail) -> Tuple[str, List[RowChange]]:
    rule_id = detail["id"]
    rule = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if rule is None or rule["created_at"] != detail["created_at"]:
        raise BooksError(f"rule {rule_id} from change {entry['id']} was already deleted. Nothing was changed.")
    labels = category_labels(conn, rule["business_id"])
    booked = conn.execute(
        "SELECT id, category_id FROM transactions WHERE rule_id = ? AND category_source = 'rule'", (rule_id,)
    ).fetchall()
    changes: List[RowChange] = []
    for row in booked:
        conn.execute(
            "UPDATE transactions SET category_id = NULL, category_source = NULL, rule_id = NULL WHERE id = ?", (row["id"],)
        )
        changes.append(
            (row["id"], booking(row["category_id"], labels.get(row["category_id"]), "rule", rule_id), booking(None, None, None, None))
        )
    conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return (
        f"rule {rule_id} ({detail['match_type']} {detail['pattern']!r}) is deleted, and the {len(changes)} row(s) it "
        "booked are back in the review queue.",
        changes,
    )


def _revert_rule_delete(conn, entry, detail) -> Tuple[str, List[RowChange]]:
    if conn.execute("SELECT 1 FROM businesses WHERE id = ?", (detail["business_id"],)).fetchone() is None:
        raise BooksError("the business that rule belonged to is gone. Nothing was changed.")
    category = conn.execute("SELECT * FROM categories WHERE id = ?", (detail["category_id"],)).fetchone()
    if category is None or not category["active"]:
        raise BooksError(f"the rule's category {detail['category']} is retired now, so the rule cannot come back.")
    if detail["account_id"] is not None and conn.execute(
        "SELECT 1 FROM accounts WHERE id = ?", (detail["account_id"],)
    ).fetchone() is None:
        raise BooksError("the account that rule was limited to is gone. Nothing was changed.")
    columns = [name for name in RULE_FIELDS if name != "id"]
    values = [detail[name] for name in columns]
    if conn.execute("SELECT 1 FROM rules WHERE id = ?", (detail["id"],)).fetchone() is None:
        columns.insert(0, "id")
        values.insert(0, detail["id"])
    cursor = conn.execute(
        f"INSERT INTO rules ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})", values
    )
    rule_id = cursor.lastrowid
    for transaction_id in detail.get("held_back", []):
        conn.execute(
            "INSERT OR IGNORE INTO rule_exceptions (rule_id, transaction_id)"
            " SELECT ?, id FROM transactions WHERE id = ?",
            (rule_id, transaction_id),
        )
    label = f"{category['code']} {category['name']}"
    changes: List[RowChange] = []
    for transaction_id in detail.get("booked", []):
        cursor = conn.execute(
            "UPDATE transactions SET rule_id = ? WHERE id = ? AND category_source = 'rule' AND rule_id IS NULL"
            " AND category_id = ?",
            (rule_id, transaction_id, category["id"]),
        )
        if cursor.rowcount:
            changes.append(
                (transaction_id, booking(category["id"], label, "rule", None), booking(category["id"], label, "rule", rule_id))
            )
    again = f"rule {rule_id}" + (f" (it was rule {detail['id']})" if rule_id != detail["id"] else "")
    return f"{again} is back: {detail['match_type']} {detail['pattern']!r} -> {label}.", changes


# Checking the record

def problems(conn: sqlite3.Connection) -> List[str]:
    """What 'check' reports about the history: entries it cannot read or that point at nothing."""
    found: List[str] = []
    unknown = [
        (row["kind"], row["n"])
        for row in conn.execute("SELECT kind, COUNT(*) AS n FROM history GROUP BY kind")
        if row["kind"] not in KINDS
    ]
    for kind, count in unknown:
        found.append(f"{count} history entr{'y is' if count == 1 else 'ies are'} of a kind this program does not know ({kind!r}).")
    unreadable = 0
    for row in conn.execute("SELECT detail_json FROM history"):
        try:
            if not isinstance(json.loads(row["detail_json"]), dict):
                unreadable += 1
        except (TypeError, ValueError):
            unreadable += 1
    if unreadable:
        found.append(f"{unreadable} history entr{'y has' if unreadable == 1 else 'ies have'} details that cannot be read.")
    dangling = conn.execute(
        "SELECT COUNT(*) FROM history h WHERE h.reverted_by IS NOT NULL AND NOT EXISTS"
        " (SELECT 1 FROM history r WHERE r.id = h.reverted_by AND r.kind = 'revert')"
    ).fetchone()[0]
    if dangling:
        found.append(f"{dangling} history entr{'y says it was' if dangling == 1 else 'ies say they were'} taken back by a change that is not there.")
    return found


def counts_by_kind(conn: sqlite3.Connection) -> Sequence[sqlite3.Row]:
    return conn.execute("SELECT kind, COUNT(*) AS n FROM history GROUP BY kind ORDER BY kind").fetchall()
