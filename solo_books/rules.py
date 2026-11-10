"""Categorization rules: the first enabled rule whose conditions all match, lowest priority number first, books the row.

A rule's conditions and actions are described in conditions.py. This module stores rules, puts them in order,
runs them over the books, and says why a rule did or did not book a row.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Container, Dict, Iterable, List, Optional, Sequence

from . import history
from .books import find_category, get_account, require_usable
# MAX_PATTERN and check_regex are still reached through this module, as they were before conditions had one.
from .conditions import (
    CONDITION_KINDS,
    GROUPS,
    MAX_PATTERN,
    TEXT_KINDS,
    Condition,
    Facts,
    account_condition,
    amount_condition,
    check_conditions,
    check_regex,
    clean_note,
    compile_stored,
    describe_all,
    direction_condition,
    explain_condition,
    stored_problem,
    text_condition,
)
from .csvmap import read_rows, read_text
from .db import transaction
from .errors import BooksError
from .money import parse_cents, plain_cents
from .text import from_spreadsheet_safe, spreadsheet_safe

MATCH_TYPES = ("contains", "starts", "exact", "regex")
RULE_CSV_COLUMNS = ("priority", "match", "pattern", "category", "direction", "min", "max", "account")
# The groups of conditions the rule CSV has columns for.
_CSV_GROUPS = {"description", "direction", "amount", "account"}

@dataclass
class Rule:
    id: int
    category_id: int
    conditions: List[Condition]
    priority: int = 100
    enabled: bool = True
    note: str = ""
    business_id: Optional[int] = None

    def _in_group(self, group: str) -> Optional[Condition]:
        for condition in self.conditions:
            if condition.group == group:
                return condition
        return None

    # The shape a rule had before it held a list of conditions, for code that reads a rule that way.

    @property
    def match_type(self) -> Optional[str]:
        wording = self._in_group("description")
        if wording is None:
            return None
        return "starts" if wording.kind == "starts_with" else wording.kind

    @property
    def pattern(self) -> Optional[str]:
        wording = self._in_group("description")
        return wording.text if wording else None

    @property
    def account_id(self) -> Optional[int]:
        account = self._in_group("account")
        return account.account_id if account else None

    @property
    def direction(self) -> Optional[str]:
        direction = self._in_group("direction")
        return direction.text if direction else None

    @property
    def min_cents(self) -> Optional[int]:
        amount = self._in_group("amount")
        return amount.low if amount else None

    @property
    def max_cents(self) -> Optional[int]:
        amount = self._in_group("amount")
        return amount.high if amount else None

    def test(self, facts: Facts) -> bool:
        """Every condition must hold. A rule left with no conditions matches nothing."""
        return bool(self.conditions) and all(condition.matches(facts) for condition in self.conditions)

    def matches(
        self,
        account_id: int,
        description: str,
        memo: str,
        merchant: str,
        amount_cents: int,
        posted_on: Optional[date] = None,
    ) -> bool:
        return self.test(Facts(account_id, description, memo, merchant, amount_cents, posted_on))

    def identity(self) -> tuple:
        """Two rules with the same identity book the same rows the same way."""
        return (tuple(sorted(condition.identity() for condition in self.conditions)), self.category_id, self.note)


def _group_order(condition: Condition) -> int:
    return GROUPS.index(condition.group) if condition.kind in CONDITION_KINDS else len(GROUPS)


def _load_conditions(conn: sqlite3.Connection, where: str, value: int) -> Dict[int, List[Condition]]:
    found: Dict[int, List[Condition]] = {}
    for row in conn.execute(
        "SELECT k.* FROM rule_conditions k JOIN rules r ON r.id = k.rule_id"
        f" WHERE {where} = ? ORDER BY k.rule_id, k.id",
        (value,),
    ):
        try:
            regex = compile_stored(row["kind"], row["text"])
        except re.error as exc:
            raise BooksError(
                f"rule {row['rule_id']} no longer compiles ({exc}). Delete it with 'rules delete {row['rule_id']}'."
            ) from None
        found.setdefault(row["rule_id"], []).append(
            Condition(row["kind"], row["text"], row["low"], row["high"], row["account_id"], regex)
        )
    for conditions in found.values():
        conditions.sort(key=_group_order)
    return found


def _rule(row: sqlite3.Row, conditions: Dict[int, List[Condition]]) -> Rule:
    return Rule(
        row["id"],
        row["category_id"],
        conditions.get(row["id"], []),
        row["priority"],
        bool(row["enabled"]),
        row["note"] or "",
        row["business_id"],
    )


def load_rules(conn: sqlite3.Connection, business_id: int, include_disabled: bool = False) -> List[Rule]:
    """A business's rules in the order they are tried. Disabled rules are left out unless asked for."""
    conditions = _load_conditions(conn, "r.business_id", business_id)
    rows = conn.execute(
        "SELECT * FROM rules WHERE business_id = ? AND (enabled = 1 OR ?) ORDER BY priority, id",
        (business_id, 1 if include_disabled else 0),
    ).fetchall()
    return [_rule(row, conditions) for row in rows]


def get_rule(conn: sqlite3.Connection, rule_id: int) -> Rule:
    row = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        raise BooksError(f"no rule {rule_id}")
    return _rule(row, _load_conditions(conn, "r.id", rule_id))


def first_match(
    rules: List[Rule],
    account_id: int,
    description: str,
    memo: str,
    merchant: str,
    amount_cents: int,
    skip: Container[int] = (),
    posted_on: Optional[date] = None,
) -> Optional[Rule]:
    facts = Facts(account_id, description, memo, merchant, amount_cents, posted_on)
    for rule in rules:
        if rule.enabled and rule.id not in skip and rule.test(facts):
            return rule
    return None


def account_names(conn: sqlite3.Connection) -> Dict[int, str]:
    return {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM accounts")}


def _check_accounts(conn: sqlite3.Connection, business_id: int, conditions: Sequence[Condition]) -> None:
    for condition in conditions:
        if condition.kind != "account":
            continue
        row = conn.execute("SELECT name, business_id FROM accounts WHERE id = ?", (condition.account_id,)).fetchone()
        if row is None:
            raise BooksError(f"no account {condition.account_id}")
        if row["business_id"] != business_id:
            raise BooksError(f"account {row['name']} belongs to another business")


def _check_category(business_id: int, category: sqlite3.Row) -> None:
    if category["business_id"] != business_id:
        raise BooksError("the rule's category belongs to another business")
    require_usable(category)


def _schema_two_columns(conditions: Sequence[Condition]) -> tuple:
    """Schema 2 kept a rule's wording in the rules table, and those columns cannot be empty, so they still get it."""
    for condition in conditions:
        if condition.kind in TEXT_KINDS:
            return ("starts" if condition.kind == "starts_with" else condition.kind, condition.text)
    return ("contains", "")


def _store_conditions(conn: sqlite3.Connection, rule_id: int, conditions: Sequence[Condition]) -> None:
    conn.execute("DELETE FROM rule_conditions WHERE rule_id = ?", (rule_id,))
    conn.executemany(
        "INSERT INTO rule_conditions (rule_id, kind, text, low, high, account_id) VALUES (?, ?, ?, ?, ?, ?)",
        [(rule_id, c.kind, c.text, c.low, c.high, c.account_id) for c in conditions],
    )


def create_rule(
    conn: sqlite3.Connection,
    business_id: int,
    conditions: Sequence[Condition],
    category: sqlite3.Row,
    priority: int = 100,
    note: str = "",
    enabled: bool = True,
    record: bool = True,
) -> int:
    """Add a rule from its conditions and actions, after checking every one of them.

    A file of rules passes record=False and keeps one history entry for the whole file.
    """
    conditions = check_conditions(conditions)
    note = clean_note(note)
    _check_category(business_id, category)
    _check_accounts(conn, business_id, conditions)
    match_type, pattern = _schema_two_columns(conditions)
    with transaction(conn):
        cursor = conn.execute(
            "INSERT INTO rules (business_id, priority, match_type, pattern, category_id, enabled, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (business_id, priority, match_type, pattern, category["id"], 1 if enabled else 0, note),
        )
        _store_conditions(conn, cursor.lastrowid, conditions)
        if record:
            rule = get_rule(conn, cursor.lastrowid)
            history.record(
                conn, "rule-add", f"Added rule {rule.id}: {said(conn, rule)}.", business_id, history.rule_detail(conn, rule.id),
            )
    return cursor.lastrowid


def add_rule(
    conn: sqlite3.Connection,
    business_id: int,
    match_type: Optional[str],
    pattern: Optional[str],
    category: sqlite3.Row,
    priority: int = 100,
    account_id: Optional[int] = None,
    direction: Optional[str] = None,
    min_cents: Optional[int] = None,
    max_cents: Optional[int] = None,
    note: str = "",
    conditions: Sequence[Condition] = (),
    record: bool = True,
) -> int:
    """Add a rule the way the rule CSV describes one, plus any further conditions.

    With match_type None the rule has no wording condition, and needs another one instead.
    """
    found: List[Condition] = []
    if match_type is not None:
        if match_type not in MATCH_TYPES:
            raise BooksError(f"match type must be one of: {', '.join(MATCH_TYPES)}")
        found.append(text_condition(match_type, pattern or ""))
    if direction is not None:
        found.append(direction_condition(direction))
    if min_cents is not None or max_cents is not None:
        found.append(amount_condition(min_cents, max_cents))
    if account_id is not None:
        found.append(account_condition(account_id))
    found.extend(conditions)
    return create_rule(conn, business_id, found, category, priority=priority, note=note, record=record)


def edit_rule(
    conn: sqlite3.Connection,
    rule_id: int,
    conditions: Sequence[Condition] = (),
    drop: Iterable[str] = (),
    category: Optional[sqlite3.Row] = None,
    note: Optional[str] = None,
) -> Rule:
    """Change a rule. A new condition replaces the rule's condition of the same group; drop removes groups.

    Rows the rule booked earlier keep their category until the rules are run again with a recheck.
    """
    rule = get_rule(conn, rule_id)
    drop = set(drop)
    unknown = drop - set(GROUPS)
    if unknown:
        raise BooksError(f"no condition group {sorted(unknown)[0]!r} (groups: {', '.join(GROUPS)})")
    replaced = {condition.group for condition in conditions}
    kept = [c for c in rule.conditions if c.group not in drop and c.group not in replaced]
    new_conditions = check_conditions([*kept, *conditions])
    business_id = rule.business_id or 0
    _check_accounts(conn, business_id, new_conditions)
    category_id = rule.category_id
    if category is not None:
        _check_category(business_id, category)
        category_id = category["id"]
    new_note = rule.note if note is None else clean_note(note)
    match_type, pattern = _schema_two_columns(new_conditions)
    with transaction(conn):
        before = history.rule_detail(conn, rule_id)
        conn.execute(
            "UPDATE rules SET category_id = ?, note = ?, match_type = ?, pattern = ?, direction = NULL,"
            " min_cents = NULL, max_cents = NULL, account_id = NULL WHERE id = ?",
            (category_id, new_note, match_type, pattern, rule_id),
        )
        _store_conditions(conn, rule_id, new_conditions)
        changed = get_rule(conn, rule_id)
        history.record(
            conn, "rule-edit", f"Changed rule {rule_id}: it is now {said(conn, changed)}.", business_id,
            {"before": before, "after": history.rule_detail(conn, rule_id)},
        )
    return changed


def move_rule(conn: sqlite3.Connection, rule_id: int, before: Optional[int] = None, position: Optional[int] = None) -> int:
    """Put a rule before another, or at a place in the order counted from 1. Returns its new place.

    The business's rules are numbered 1, 2, 3 and so on afterward, in their new order.
    """
    if (before is None) == (position is None):
        raise BooksError("say where the rule goes: before another rule, or to a place in the order")
    rule = get_rule(conn, rule_id)
    order = [other.id for other in load_rules(conn, rule.business_id or 0, include_disabled=True)]
    order.remove(rule_id)
    if before is not None:
        if before == rule_id:
            raise BooksError("a rule cannot go before itself")
        target = get_rule(conn, before)
        if target.business_id != rule.business_id:
            raise BooksError(f"rule {before} belongs to another business")
        index = order.index(before)
    else:
        assert position is not None
        if not 1 <= position <= len(order) + 1:
            raise BooksError(f"the place must be from 1 to {len(order) + 1}")
        index = position - 1
    order.insert(index, rule_id)
    with transaction(conn):
        before = {other.id: other.priority for other in load_rules(conn, rule.business_id or 0, include_disabled=True)}
        conn.executemany("UPDATE rules SET priority = ? WHERE id = ?", [(n, rid) for n, rid in enumerate(order, 1)])
        history.record(
            conn, "rule-move", f"Moved rule {rule_id} to number {index + 1} in the order.", rule.business_id,
            {"rule_id": rule_id, "place": index + 1, "priorities_before": before, "order_after": order},
        )
    return index + 1


def set_enabled(conn: sqlite3.Connection, rule_id: int, enabled: bool) -> None:
    """A disabled rule keeps its place and its rows, and is skipped until it is enabled again."""
    rule = get_rule(conn, rule_id)
    if rule.enabled == enabled:
        return
    with transaction(conn):
        conn.execute("UPDATE rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))
        kind = "rule-enable" if enabled else "rule-disable"
        history.record(
            conn, kind, f"{'Enabled' if enabled else 'Disabled'} rule {rule_id}: {said(conn, rule)}.", rule.business_id,
            {"rule_id": rule_id},
        )


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


_WORDING_KINDS = "('contains', 'starts_with', 'exact', 'regex')"
_AMOUNT_KINDS = "('amount_equals', 'amount_at_least', 'amount_at_most', 'amount_between')"


def _one(column: str, kinds: str) -> str:
    return f"(SELECT k.{column} FROM rule_conditions k WHERE k.rule_id = r.id AND k.kind IN {kinds} ORDER BY k.id LIMIT 1)"


def list_rules(conn: sqlite3.Connection, business_id: int) -> List[sqlite3.Row]:
    """A business's rules in order, with the columns the rule CSV uses, the category, and the rows each booked."""
    wording_kind = _one("kind", _WORDING_KINDS)
    direction = _one("text", "('direction')")
    account_id = _one("account_id", "('account')")
    return conn.execute(
        "SELECT r.id, r.business_id, r.priority, r.category_id, r.enabled, r.note, r.created_at,"
        f" CASE {wording_kind} WHEN 'starts_with' THEN 'starts' ELSE {wording_kind} END AS match_type,"
        f" {_one('text', _WORDING_KINDS)} AS pattern, {direction} AS direction,"
        f" {_one('low', _AMOUNT_KINDS)} AS min_cents, {_one('high', _AMOUNT_KINDS)} AS max_cents,"
        f" {account_id} AS account_id, c.code AS category_code, c.name AS category_name,"
        f" (SELECT a.name FROM accounts a WHERE a.id = {account_id}) AS account_name,"
        " (SELECT COUNT(*) FROM transactions t WHERE t.rule_id = r.id) AS hits"
        " FROM rules r JOIN categories c ON c.id = r.category_id"
        " WHERE r.business_id = ? ORDER BY r.priority, r.id",
        (business_id,),
    ).fetchall()


def said(conn: sqlite3.Connection, rule: Rule) -> str:
    """A rule in one line: its conditions, the category it books, and its note."""
    category = conn.execute("SELECT code, name FROM categories WHERE id = ?", (rule.category_id,)).fetchone()
    booked = f"{category['code']} {category['name']}" if category else f"category {rule.category_id}"
    line = f"{describe_all(rule.conditions, account_names(conn))} -> {booked}"
    return line + (f", note {rule.note!r}" if rule.note else "")


def describe_rule(conn: sqlite3.Connection, rule_id: int) -> Optional[str]:
    """A rule's conditions in words, or None when there is no such rule."""
    try:
        rule = get_rule(conn, rule_id)
    except BooksError:
        return None
    return describe_all(rule.conditions, account_names(conn))


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> None:
    """Rows the rule already booked keep their category."""
    rule = get_rule(conn, rule_id)
    with transaction(conn):
        detail = history.rule_detail(conn, rule_id)
        detail["held_back"] = [
            row[0] for row in conn.execute("SELECT transaction_id FROM rule_exceptions WHERE rule_id = ?", (rule_id,))
        ]
        detail["booked"] = [
            row[0] for row in conn.execute("SELECT id FROM transactions WHERE rule_id = ? AND category_source = 'rule'", (rule_id,))
        ]
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        history.record(
            conn, "rule-delete",
            f"Deleted rule {rule_id} ({describe_all(rule.conditions, account_names(conn))}); "
            f"its {len(detail['booked'])} row(s) keep their category.",
            rule.business_id, detail,
        )


def _held_back(conn: sqlite3.Connection, business_id: int) -> Dict[int, set]:
    held: Dict[int, set] = {}
    for exception in conn.execute(
        "SELECT e.rule_id, e.transaction_id FROM rule_exceptions e JOIN rules r ON r.id = e.rule_id"
        " WHERE r.business_id = ?",
        (business_id,),
    ):
        held.setdefault(exception["transaction_id"], set()).add(exception["rule_id"])
    return held


def _facts(row: sqlite3.Row) -> Facts:
    return Facts(
        row["account_id"], row["description"], row["memo"], row["merchant"], row["amount_cents"],
        date.fromisoformat(row["posted_on"]),
    )


def _note_after(row: sqlite3.Row, rule: Optional[Rule]) -> Optional[tuple]:
    """The note a row should carry after a rule run, as (note, from_rule), or None to leave it.

    A rule's note goes on a row with no note, or replaces an earlier rule's. A note someone wrote stays.
    """
    if rule is not None and rule.note:
        if row["note"] and not row["note_from_rule"]:
            return None
        if row["note"] == rule.note and row["note_from_rule"]:
            return None
        return (rule.note, 1)
    if row["note_from_rule"]:
        return ("", 0)
    return None


def apply_rules(conn: sqlite3.Connection, business_id: int, recheck: bool = False) -> dict:
    """Run the rules over rows waiting for review; with recheck, over rule-booked rows as well.

    Rows someone categorized by hand are never touched, and neither are rows held back from a rule.
    """
    rules = load_rules(conn, business_id)
    held_back = _held_back(conn, business_id)
    condition = "t.category_id IS NULL"
    if recheck:
        condition = "(t.category_id IS NULL OR t.category_source = 'rule')"
    rows = conn.execute(
        "SELECT t.id, t.account_id, t.posted_on, t.description, t.memo, t.merchant, t.amount_cents, t.category_id,"
        " t.rule_id, t.category_source, t.note, t.note_from_rule FROM transactions t JOIN accounts a ON a.id = t.account_id"
        f" WHERE a.business_id = ? AND {condition}",
        (business_id,),
    ).fetchall()
    categorized = changed = cleared = 0
    labels = history.category_labels(conn, business_id)
    changes = []
    with transaction(conn):
        for row in rows:
            before = history.booking(row["category_id"], labels.get(row["category_id"]), row["category_source"], row["rule_id"])
            facts = _facts(row)
            skip = held_back.get(row["id"], ())
            rule = next((r for r in rules if r.id not in skip and r.test(facts)), None)
            note = _note_after(row, rule)
            if note is not None:
                conn.execute("UPDATE transactions SET note = ?, note_from_rule = ? WHERE id = ?", (*note, row["id"]))
                changes.append((row["id"], {"note": row["note"]}, {"note": note[0]}))
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


# Explaining a row

@dataclass
class Trial:
    """One rule, as tried against one row."""

    place: int
    rule: Rule
    lines: List[str]
    matched: bool
    held_back: bool
    verdict: str = ""


@dataclass
class Explanation:
    transaction_id: int
    facts: Facts
    business_id: int
    trials: List[Trial] = field(default_factory=list)
    winner: Optional[Rule] = None


def explain(conn: sqlite3.Connection, transaction_id: int, facts: Optional[Facts] = None) -> Explanation:
    """Every rule of the row's business in order, whether each condition held, and which rule books the row."""
    row = conn.execute(
        "SELECT t.*, a.business_id FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.id = ?",
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise BooksError(f"no transaction {transaction_id}")
    held = _held_back(conn, row["business_id"]).get(transaction_id, set())
    return explain_facts(conn, row["business_id"], facts or _facts(row), held, transaction_id)


def explain_facts(
    conn: sqlite3.Connection, business_id: int, facts: Facts, held: Container[int] = (), transaction_id: int = 0
) -> Explanation:
    names = account_names(conn)
    result = Explanation(transaction_id, facts, business_id)
    for place, rule in enumerate(load_rules(conn, business_id, include_disabled=True), start=1):
        lines = [explain_condition(condition, facts, names) for condition in rule.conditions]
        lines = lines or ["no conditions, so it matches nothing"]
        trial = Trial(place, rule, lines, rule.test(facts), rule.id in held)
        if not rule.enabled:
            trial.verdict = "disabled, so it is skipped" + (" (it would match)" if trial.matched else "")
        elif trial.held_back:
            trial.verdict = "this row is held back from it" + (" (it would match)" if trial.matched else "")
        elif not trial.matched:
            trial.verdict = "does not match"
        elif result.winner is None:
            result.winner = rule
            trial.verdict = "MATCHES, and books the row"
        else:
            trial.verdict = f"matches too, but rule {result.winner.id} comes first"
        result.trials.append(trial)
    return result


# Rule files: the CSV columns rules have always had

def export_rules(conn: sqlite3.Connection, business_id: int) -> str:
    """The rules as CSV. A rule the columns cannot carry stops the export, so nothing is quietly left out."""
    rows = list_rules(conn, business_id)
    unfit = []
    for rule in load_rules(conn, business_id, include_disabled=True):
        groups = {condition.group for condition in rule.conditions}
        if not rule.enabled or rule.note or rule.match_type is None or not groups <= _CSV_GROUPS:
            unfit.append(str(rule.id))
    if unfit:
        raise BooksError(
            f"rule(s) {', '.join(unfit)} use a condition, a note or a disabled state that the CSV columns cannot carry. "
            "Export with --format json instead, which carries everything."
        )
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(RULE_CSV_COLUMNS)
    for row in rows:
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


def problems(conn: sqlite3.Connection) -> List[str]:
    """What 'check' reports about rules: one with no conditions, two conditions of one group, a condition no command
    could have made, or one limited to another business's account."""
    found: List[str] = []
    empty = conn.execute(
        "SELECT COUNT(*) FROM rules r WHERE NOT EXISTS (SELECT 1 FROM rule_conditions k WHERE k.rule_id = r.id)"
    ).fetchone()[0]
    if empty:
        found.append(f"{empty} rule(s) have no conditions, so they book nothing. Edit them with 'rules edit' or delete them.")
    bad, doubled = [], set()
    groups: Dict[tuple, int] = {}
    for row in conn.execute("SELECT * FROM rule_conditions ORDER BY rule_id, id"):
        condition = Condition(row["kind"], row["text"], row["low"], row["high"], row["account_id"])
        problem = stored_problem(condition)
        if problem:
            bad.append(f"rule {row['rule_id']}: a condition {problem}")
            continue
        key = (row["rule_id"], condition.group)
        groups[key] = groups.get(key, 0) + 1
        if groups[key] > 1:
            doubled.add(row["rule_id"])
    if bad:
        found.append(f"{len(bad)} rule condition(s) cannot be used: {bad[0]}" + (" (and more)" if len(bad) > 1 else "") + ".")
    if doubled:
        found.append(f"{len(doubled)} rule(s) have two conditions of the same kind, such as two amounts.")
    crossed = conn.execute(
        "SELECT COUNT(*) FROM rule_conditions k JOIN rules r ON r.id = k.rule_id JOIN accounts a ON a.id = k.account_id"
        " WHERE a.business_id != r.business_id"
    ).fetchone()[0]
    if crossed:
        found.append(f"{crossed} rule(s) are limited to another business's account.")
    return found
