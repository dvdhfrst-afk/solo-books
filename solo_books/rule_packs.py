"""Rule packs: a business's rules as a versioned JSON file that carries every condition and action.

A pack can move rules between businesses or books, or be kept as a copy. Importing one checks every rule before it
adds any, and skips a rule the business already has with the same conditions and actions, so importing the same
pack twice adds nothing the second time.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from . import history
from .books import find_category, get_account
from .conditions import ACTION_KINDS, CONDITION_KINDS, Condition, check_conditions, clean_note, condition_from_json
from .db import transaction
from .errors import BooksError
from .rules import Rule, account_names, create_rule, load_rules

PACK_VERSION = 1
# Far larger than any real set of rules, and small enough that a wrong file is refused before it fills memory.
MAX_PACK_BYTES = 8 * 1024 * 1024
_RULE_KEYS = {"conditions", "actions", "priority", "enabled"}


@dataclass
class PackImport:
    added: int = 0
    skipped: int = 0


@dataclass
class _Planned:
    conditions: List[Condition]
    category: sqlite3.Row
    note: str
    priority: int
    enabled: bool


def export_pack(conn: sqlite3.Connection, business_id: int) -> str:
    names = account_names(conn)
    codes = {
        row["id"]: row["code"]
        for row in conn.execute("SELECT id, code FROM categories WHERE business_id = ?", (business_id,))
    }
    packed = []
    for rule in load_rules(conn, business_id, include_disabled=True):
        actions = [{"kind": "book_category", "category": codes[rule.category_id]}]
        if rule.note:
            actions.append({"kind": "set_note", "note": rule.note})
        try:
            conditions = [CONDITION_KINDS[c.kind].to_json(c, names) for c in rule.conditions]
        except BooksError as exc:
            raise BooksError(f"rule {rule.id}: {exc}") from None
        packed.append({"priority": rule.priority, "enabled": rule.enabled, "conditions": conditions, "actions": actions})
    return json.dumps({"pack_version": PACK_VERSION, "rules": packed}, indent=2, ensure_ascii=False) + "\n"


def _read(path: Path) -> object:
    name = path.name
    try:
        if path.stat().st_size > MAX_PACK_BYTES:
            raise BooksError(f"{name} is too large to be a rule pack")
        raw = path.read_bytes()
    except OSError as exc:
        raise BooksError(f"cannot read {name}: {exc.strerror or exc}") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise BooksError(f"{name} is not UTF-8 text, so it is not a rule pack") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise BooksError(f"{name} is not valid JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})") from None


def _whole_number(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BooksError(f"{what} must be a whole number")
    return value


def _plan_rule(conn: sqlite3.Connection, business_id: int, data: object) -> _Planned:
    if not isinstance(data, dict):
        raise BooksError("each rule must be a JSON object")
    extra = set(data) - _RULE_KEYS
    if extra:
        raise BooksError(f"unknown key(s): {', '.join(sorted(extra))}")
    priority = _whole_number(data.get("priority", 100), "priority")
    if abs(priority) > 1_000_000_000:
        raise BooksError("priority is too large")
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise BooksError("enabled must be true or false")

    def find_account(name: str) -> int:
        account = get_account(conn, name)
        if account["business_id"] != business_id:
            raise BooksError(f"account {name!r} belongs to another business")
        return account["id"]

    raw_conditions = data.get("conditions")
    if not isinstance(raw_conditions, list):
        raise BooksError("needs a list of conditions")
    conditions = check_conditions([condition_from_json(item, find_account) for item in raw_conditions])
    raw_actions = data.get("actions")
    if not isinstance(raw_actions, list):
        raise BooksError("needs a list of actions")
    category: Optional[sqlite3.Row] = None
    note = ""
    seen = set()
    for action in raw_actions:
        if not isinstance(action, dict) or action.get("kind") not in ACTION_KINDS:
            kind = action.get("kind") if isinstance(action, dict) else action
            raise BooksError(f"unknown action {kind!r} (actions: {', '.join(ACTION_KINDS)})")
        kind = action["kind"]
        if kind in seen:
            raise BooksError(f"has two {kind} actions")
        seen.add(kind)
        wanted = "category" if kind == "book_category" else "note"
        extra = set(action) - {"kind", wanted}
        if extra:
            raise BooksError(f"a {kind} action has unknown key(s): {', '.join(sorted(extra))}")
        value = action.get(wanted)
        if not isinstance(value, str):
            raise BooksError(f"a {kind} action needs {wanted!r} as text")
        if kind == "book_category":
            category = find_category(conn, business_id, value)
            if not category["active"]:
                raise BooksError(f"category {category['code']} {category['name']} is retired")
        else:
            note = clean_note(value)
    if category is None:
        raise BooksError("needs a book_category action")
    return _Planned(conditions, category, note, priority, enabled)


def import_pack(conn: sqlite3.Connection, business_id: int, path: Path) -> PackImport:
    """Add a pack's rules to a business. Every rule is checked first; one bad rule adds none."""
    path = Path(path)
    data = _read(path)
    if not isinstance(data, dict) or "pack_version" not in data:
        raise BooksError(f"{path.name} is not a rule pack: it has no pack_version")
    version = data["pack_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise BooksError(f"{path.name} has a pack_version that is not a version number")
    if version > PACK_VERSION:
        raise BooksError(
            f"{path.name} is a version {version} rule pack, and this version of solo-books reads up to version "
            f"{PACK_VERSION}. Install the newer version."
        )
    extra = set(data) - {"pack_version", "rules"}
    if extra:
        raise BooksError(f"{path.name} has unknown key(s): {', '.join(sorted(extra))}")
    if not isinstance(data.get("rules"), list):
        raise BooksError(f"{path.name} needs a list of rules")
    planned = []
    for number, item in enumerate(data["rules"], start=1):
        try:
            planned.append(_plan_rule(conn, business_id, item))
        except BooksError as exc:
            raise BooksError(f"{path.name} rule {number}: {exc}. No rules were added.") from None
    have = {rule.identity() for rule in load_rules(conn, business_id, include_disabled=True)}
    outcome = PackImport()
    added: List[int] = []
    with transaction(conn):
        for plan in planned:
            identity = Rule(0, plan.category["id"], plan.conditions, note=plan.note).identity()
            if identity in have:
                outcome.skipped += 1
                continue
            rule_id = create_rule(
                conn, business_id, plan.conditions, plan.category, priority=plan.priority, note=plan.note,
                enabled=plan.enabled, record=False,
            )
            have.add(identity)
            outcome.added += 1
            added.append(rule_id)
        if added:
            history.record(
                conn, "rule-import", f"Added {len(added)} rule(s) from {path.name}.", business_id,
                {"file_name": path.name, "rule_ids": added, "skipped": outcome.skipped},
            )
    return outcome
