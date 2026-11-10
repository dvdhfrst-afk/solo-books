"""Categorization rules: the first matching rule, lowest priority number first, sets the category."""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Container, Dict, Iterable, List, Optional, Pattern

from . import history
from .books import find_category, get_account, require_usable
from .csvmap import read_rows, read_text
from .db import transaction
from .errors import BooksError
from .money import parse_cents, plain_cents
from .text import clean_cell, from_spreadsheet_safe, normalize_description, spreadsheet_safe

MATCH_TYPES = ("contains", "starts", "exact", "regex")
RULE_CSV_COLUMNS = ("priority", "match", "pattern", "category", "direction", "min", "max", "account")
MAX_PATTERN = 300
# A repeat inside a repeat, such as (a+)+, can take years to fail on an ordinary line of text.
_NESTED_REPEAT = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d*,?\d*\})")


@dataclass
class Rule:
    id: int
    account_id: Optional[int]
    match_type: str
    pattern: str
    direction: Optional[str]
    min_cents: Optional[int]
    max_cents: Optional[int]
    category_id: int
    _regex: Optional[Pattern[str]] = None

    def matches(self, account_id: int, description: str, memo: str, merchant: str, amount_cents: int) -> bool:
        if self.account_id is not None and self.account_id != account_id:
            return False
        if self.direction == "in" and amount_cents <= 0:
            return False
        if self.direction == "out" and amount_cents >= 0:
            return False
        size = abs(amount_cents)
        if self.min_cents is not None and size < self.min_cents:
            return False
        if self.max_cents is not None and size > self.max_cents:
            return False
        text = normalize_description(description)
        if self.match_type == "regex":
            assert self._regex is not None
            return bool(self._regex.search(text) or (memo and self._regex.search(normalize_description(memo))))
        wanted = normalize_description(self.pattern)
        if self.match_type == "exact":
            return text == wanted or merchant == wanted
        if self.match_type == "starts":
            return text.startswith(wanted)
        # A merchant name can skip a store number that sits between its words, so check it too.
        return wanted in text or wanted in merchant or (bool(memo) and wanted in normalize_description(memo))


def check_regex(pattern: str) -> None:
    """Refuse a pattern that does not compile, or whose shape can hang a whole import."""
    if _NESTED_REPEAT.search(pattern):
        raise BooksError(
            f"regular expression {pattern!r} has a repeat inside a repeat, which can run for years on one line of "
            "text. Rewrite it without the outer repeat."
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise BooksError(f"bad regular expression {pattern!r}: {exc}") from None


def _compile(row: sqlite3.Row) -> Rule:
    regex = None
    if row["match_type"] == "regex":
        try:
            regex = re.compile(row["pattern"], re.IGNORECASE)
        except re.error as exc:
            raise BooksError(f"rule {row['id']} no longer compiles ({exc}). Delete it with 'rules delete {row['id']}'.") from None
    return Rule(
        row["id"],
        row["account_id"],
        row["match_type"],
        row["pattern"],
        row["direction"],
        row["min_cents"],
        row["max_cents"],
        row["category_id"],
        regex,
    )


def load_rules(conn: sqlite3.Connection, business_id: int) -> List[Rule]:
    rows = conn.execute("SELECT * FROM rules WHERE business_id = ? ORDER BY priority, id", (business_id,)).fetchall()
    return [_compile(row) for row in rows]


def first_match(
    rules: List[Rule],
    account_id: int,
    description: str,
    memo: str,
    merchant: str,
    amount_cents: int,
    skip: Container[int] = (),
) -> Optional[Rule]:
    for rule in rules:
        if rule.id not in skip and rule.matches(account_id, description, memo, merchant, amount_cents):
            return rule
    return None


def add_rule(
    conn: sqlite3.Connection,
    business_id: int,
    match_type: str,
    pattern: str,
    category: sqlite3.Row,
    priority: int = 100,
    account_id: Optional[int] = None,
    direction: Optional[str] = None,
    min_cents: Optional[int] = None,
    max_cents: Optional[int] = None,
    record: bool = True,
) -> int:
    """Add one rule. 'rules import' passes record=False and keeps one history entry for the whole file."""
    if match_type not in MATCH_TYPES:
        raise BooksError(f"match type must be one of: {', '.join(MATCH_TYPES)}")
    pattern = clean_cell(pattern)
    if not pattern:
        raise BooksError("a rule needs a pattern")
    if len(pattern) > MAX_PATTERN:
        raise BooksError(f"a rule's pattern can be at most {MAX_PATTERN} characters")
    if match_type == "regex":
        check_regex(pattern)
    if direction not in (None, "in", "out"):
        raise BooksError("direction must be in or out")
    if min_cents is not None and max_cents is not None and min_cents > max_cents:
        raise BooksError("minimum amount is above the maximum")
    if category["business_id"] != business_id:
        raise BooksError("the rule's category belongs to another business")
    require_usable(category)
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO rules (business_id, account_id, priority, match_type, pattern, direction,"
            " min_cents, max_cents, category_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (business_id, account_id, priority, match_type, pattern, direction, min_cents, max_cents, category["id"]),
        )
        if record:
            rule = conn.execute("SELECT * FROM rules WHERE id = ?", (cursor.lastrowid,)).fetchone()
            history.record(
                conn, "rule-add", f"Added rule {rule['id']}: {match_type} {pattern!r} -> {category['code']} {category['name']}.",
                business_id, history.rule_detail(conn, rule),
            )
    return cursor.lastrowid


def hold_back(conn: sqlite3.Connection, rule_id: int, transaction_ids: Iterable[int]) -> None:
    """This rule never books these rows. A person can still book them by hand."""
    ids = list(transaction_ids)
    if not ids:
        return
    with transaction(conn):
        conn.executemany(
            "INSERT OR IGNORE INTO rule_exceptions (rule_id, transaction_id) VALUES (?, ?)",
            [(rule_id, transaction_id) for transaction_id in ids],
        )
        business_id = conn.execute("SELECT business_id FROM rules WHERE id = ?", (rule_id,)).fetchone()[0]
        history.record(
            conn, "rule-hold-back", f"Held {len(ids)} row(s) back from rule {rule_id}.", business_id, {"rule_id": rule_id},
            [(transaction_id, None, {"held_back_from_rule": rule_id}) for transaction_id in ids],
        )


def list_rules(conn: sqlite3.Connection, business_id: int) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT r.*, c.code AS category_code, c.name AS category_name, a.name AS account_name,"
        " (SELECT COUNT(*) FROM transactions t WHERE t.rule_id = r.id) AS hits"
        " FROM rules r JOIN categories c ON c.id = r.category_id"
        " LEFT JOIN accounts a ON a.id = r.account_id"
        " WHERE r.business_id = ? ORDER BY r.priority, r.id",
        (business_id,),
    ).fetchall()


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    """Rows the rule already booked keep their category."""
    rule = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if rule is None:
        raise BooksError(f"no rule {rule_id}")
    with transaction(conn):
        detail = history.rule_detail(conn, rule)
        detail["held_back"] = [
            row[0] for row in conn.execute("SELECT transaction_id FROM rule_exceptions WHERE rule_id = ?", (rule_id,))
        ]
        detail["booked"] = [
            row[0] for row in conn.execute("SELECT id FROM transactions WHERE rule_id = ? AND category_source = 'rule'", (rule_id,))
        ]
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        history.record(
            conn, "rule-delete",
            f"Deleted rule {rule_id} ({rule['match_type']} {rule['pattern']!r}); its {len(detail['booked'])} row(s) keep their category.",
            rule["business_id"], detail,
        )


def apply_rules(conn: sqlite3.Connection, business_id: int, recheck: bool = False) -> dict:
    """Run the rules over rows waiting for review; with recheck, over rule-booked rows as well.

    Rows someone categorized by hand are never touched, and neither are rows held back from a rule.
    """
    rules = load_rules(conn, business_id)
    held_back: Dict[int, set] = {}
    for exception in conn.execute(
        "SELECT e.rule_id, e.transaction_id FROM rule_exceptions e JOIN rules r ON r.id = e.rule_id"
        " WHERE r.business_id = ?",
        (business_id,),
    ):
        held_back.setdefault(exception["transaction_id"], set()).add(exception["rule_id"])
    condition = "t.category_id IS NULL"
    if recheck:
        condition = "(t.category_id IS NULL OR t.category_source = 'rule')"
    rows = conn.execute(
        "SELECT t.id, t.account_id, t.description, t.memo, t.merchant, t.amount_cents, t.category_id, t.rule_id,"
        " t.category_source"
        " FROM transactions t JOIN accounts a ON a.id = t.account_id"
        f" WHERE a.business_id = ? AND {condition}",
        (business_id,),
    ).fetchall()
    categorized = changed = cleared = 0
    labels = history.category_labels(conn, business_id)
    changes = []
    with transaction(conn):
        for row in rows:
            before = history.booking(row["category_id"], labels.get(row["category_id"]), row["category_source"], row["rule_id"])
            rule = first_match(
                rules,
                row["account_id"],
                row["description"],
                row["memo"],
                row["merchant"],
                row["amount_cents"],
                held_back.get(row["id"], ()),
            )
            if rule is None:
                if row["category_id"] is not None:
                    conn.execute(
                        "UPDATE transactions SET category_id = NULL, category_source = NULL, rule_id = NULL WHERE id = ?",
                        (row["id"],),
                    )
                    cleared += 1
                    changes.append((row["id"], before, history.booking(None, None, None, None)))
                continue
            if row["category_id"] == rule.category_id and row["rule_id"] == rule.id:
                continue
            conn.execute(
                "UPDATE transactions SET category_id = ?, category_source = 'rule', rule_id = ? WHERE id = ?",
                (rule.category_id, rule.id, row["id"]),
            )
            changes.append((row["id"], before, history.booking(rule.category_id, labels.get(rule.category_id), "rule", rule.id)))
            if row["category_id"] is None:
                categorized += 1
            elif row["category_id"] != rule.category_id:
                changed += 1
        if changes:
            history.record(
                conn, "rule-apply",
                f"Rules booked {categorized} row(s), changed {changed} and sent {cleared} back to review.",
                business_id, {"recheck": recheck, "categorized": categorized, "changed": changed, "returned_to_review": cleared},
                changes,
            )
    return {"categorized": categorized, "changed": changed, "returned_to_review": cleared}


def export_rules(conn: sqlite3.Connection, business_id: int) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(RULE_CSV_COLUMNS)
    for row in list_rules(conn, business_id):
        writer.writerow(
            [
                row["priority"],
                row["match_type"],
                spreadsheet_safe(row["pattern"]),
                row["category_code"],
                row["direction"] or "",
                plain_cents(row["min_cents"]) if row["min_cents"] is not None else "",
                plain_cents(row["max_cents"]) if row["max_cents"] is not None else "",
                row["account_name"] or "",
            ]
        )
    return out.getvalue()


def import_rules(conn: sqlite3.Connection, business_id: int, path: Path) -> int:
    """Add rules from a CSV with the same columns 'rules export' writes. All or nothing."""
    name = Path(path).name
    text = read_text(path, None)
    # Going through the whole file first means a malformed one stops before anything is added.
    if not read_rows(text, ",", name):
        raise BooksError(f"{name} is empty")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    missing = {"match", "pattern", "category"} - set(reader.fieldnames or [])
    if missing:
        raise BooksError(f"{Path(path).name} is missing column(s): {', '.join(sorted(missing))}")
    added: List[int] = []
    with transaction(conn):
        for line, row in enumerate(reader, start=2):
            try:
                category = find_category(conn, business_id, row["category"] or "")
                account_id = None
                if (row.get("account") or "").strip():
                    account = get_account(conn, row["account"])
                    if account["business_id"] != business_id:
                        raise BooksError(f"account {row['account']!r} belongs to another business")
                    account_id = account["id"]
                rule_id = add_rule(
                    conn,
                    business_id,
                    (row["match"] or "").strip(),
                    from_spreadsheet_safe(row["pattern"] or ""),
                    category,
                    priority=int(row.get("priority") or 100),
                    account_id=account_id,
                    direction=(row.get("direction") or "").strip() or None,
                    min_cents=parse_cents(row["min"]) if (row.get("min") or "").strip() else None,
                    max_cents=parse_cents(row["max"]) if (row.get("max") or "").strip() else None,
                    record=False,
                )
            except (BooksError, ValueError) as exc:
                raise BooksError(f"{Path(path).name} line {line}: {exc}. No rules were added.") from None
            added.append(rule_id)
        if added:
            history.record(
                conn, "rule-import", f"Added {len(added)} rule(s) from {name}.", business_id,
                {"file_name": name, "rule_ids": added},
            )
    return len(added)
