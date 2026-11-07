"""The review queue: rows no rule could book, grouped by merchant so one decision covers many."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from .books import find_category, list_categories
from .db import transaction
from .errors import BooksError
from .ledger import categorize, find_transactions, get_transaction
from .money import format_cents
from .rules import add_rule, apply_rules, hold_back
from .text import names_a_payee

_ROWS_LISTED = 10


@dataclass
class Group:
    business_id: int
    business: str
    merchant: str
    rows: List[sqlite3.Row] = field(default_factory=list)

    @property
    def total_cents(self) -> int:
        return sum(row["amount_cents"] for row in self.rows)


def queue(conn: sqlite3.Connection, business_id: Optional[int] = None, account_id: Optional[int] = None) -> List[sqlite3.Row]:
    return find_transactions(conn, business_id=business_id, account_id=account_id, uncategorized=True)


def group_rows(rows: Sequence[sqlite3.Row]) -> List[Group]:
    """Largest groups first, because they clear the most rows per decision."""
    groups: Dict[tuple, Group] = {}
    for row in rows:
        key = (row["business_id"], row["merchant"])
        if key not in groups:
            groups[key] = Group(row["business_id"], row["business_name"], row["merchant"])
        groups[key].rows.append(row)
    return sorted(groups.values(), key=lambda g: (-len(g.rows), g.business, g.merchant))


def book_rows(
    conn: sqlite3.Connection,
    business_id: int,
    transaction_ids: Sequence[int],
    category: sqlite3.Row,
    rule_pattern: Optional[str] = None,
    held_back: Sequence[int] = (),
) -> dict:
    """Book chosen rows. With a rule pattern, also make the rule and run it over the rest of the queue.

    Rows in held_back stay in the queue, and the new rule will not book them later either.
    """
    if rule_pattern and not names_a_payee(rule_pattern):
        raise BooksError(
            f"{rule_pattern!r} says how the money moved, not who it went to, so a rule on it would catch other payees' rows. "
            "Nothing was booked. Book these rows without a rule, or use 'rules add' with wording only this payee has."
        )
    if set(held_back) & set(transaction_ids):
        raise BooksError("a row cannot be both booked and held back")
    for transaction_id in held_back:
        if get_transaction(conn, transaction_id)["business_id"] != business_id:
            raise BooksError(f"transaction {transaction_id} belongs to another business")
    result = {"booked": 0, "by_rule": 0, "rule_id": None}
    with transaction(conn):
        if rule_pattern:
            # Exactly this merchant. "contains" would let a short name such as PAYMENT catch other people's rows.
            result["rule_id"] = add_rule(conn, business_id, "exact", rule_pattern, category)
            hold_back(conn, result["rule_id"], held_back)
            result["by_rule"] = apply_rules(conn, business_id)["categorized"]
        still_open = [
            transaction_id
            for transaction_id in transaction_ids
            if conn.execute(
                "SELECT 1 FROM transactions WHERE id = ? AND category_id IS NULL", (transaction_id,)
            ).fetchone()
        ]
        result["booked"] = categorize(conn, still_open, category)
    return result


HELP = """  CODE or NAME   book this row to that category (three letters of the name are enough if only one fits)
  a CODE         book this row and every queued row from the same merchant
  r CODE         same as a, and make a rule so future rows from exactly this merchant book themselves
  t              book this row to Transfer (card payments, moves between accounts)
  l              list categories
  s              skip for now
  q              quit (everything booked so far is saved)
"""


def run_interactive(
    conn: sqlite3.Connection,
    business_id: Optional[int],
    account_id: Optional[int],
    read: Callable[[str], str],
    write: Callable[[str], None],
) -> dict:
    """Walk the queue one row at a time. Each answer is saved as it is given."""
    totals = {"booked": 0, "skipped": 0}
    # The rows are read once. After that only what left the queue is looked up, so long queues answer quickly.
    rows = queue(conn, business_id, account_id)
    while True:
        waiting = {found[0] for found in conn.execute("SELECT id FROM transactions WHERE category_id IS NULL")}
        rows = [row for row in rows if row["id"] in waiting]
        if not rows:
            break
        row = rows[0]
        group = [other for other in rows if other["merchant"] == row["merchant"] and other["business_id"] == row["business_id"]]
        same = [other["id"] for other in group]
        write(
            f"\n[{len(rows)} left] {row['posted_on']}  {format_cents(row['amount_cents']):>12}  "
            f"{row['account_name']} ({row['business_name']})\n  {row['description']}"
        )
        if row["memo"]:
            write(f"  memo: {row['memo']}")
        if len(same) > 1:
            total = format_cents(sum(other["amount_cents"] for other in group))
            write(f"  {len(same)} queued rows from {row['merchant']}, total {total}. 'a' or 'r' books all of them:")
            for other in group[:_ROWS_LISTED]:
                write(f"    {other['posted_on']}  {format_cents(other['amount_cents']):>12}  {other['description']}")
            if len(group) > _ROWS_LISTED:
                write(f"    ...and {len(group) - _ROWS_LISTED} more")
        try:
            answer = read("category (? for help)> ").strip()
        except EOFError:
            break
        if not answer:
            continue
        lowered = answer.lower()
        if lowered == "q":
            break
        if lowered in ("?", "h", "help"):
            write(HELP)
            continue
        if lowered == "s":
            rows.remove(row)
            totals["skipped"] += 1
            continue
        if lowered == "l":
            for category in list_categories(conn, row["business_id"]):
                write(f"  {category['code']}  {category['name']}")
            continue
        if lowered in ("a", "r"):
            write(f"  {lowered} needs a category after it, such as: {lowered} 6150. Type ? for help.")
            continue
        try:
            if lowered == "t":
                category = _transfer_category(conn, row["business_id"])
                booked = categorize(conn, [row["id"]], category)
            elif lowered.startswith("a "):
                category = find_category(conn, row["business_id"], answer[2:])
                booked = categorize(conn, same, category)
            elif lowered.startswith("r "):
                category = find_category(conn, row["business_id"], answer[2:])
                outcome = book_rows(conn, row["business_id"], same, category, rule_pattern=row["merchant"])
                booked = outcome["booked"] + outcome["by_rule"]
                write(f"  rule added: merchant is exactly {row['merchant']!r}")
            else:
                category = find_category(conn, row["business_id"], answer)
                booked = categorize(conn, [row["id"]], category)
            totals["booked"] += booked
            write(f"  {booked} row(s) booked to {category['code']} {category['name']}")
        except BooksError as exc:
            write(f"  {exc}")
    return totals


def _transfer_category(conn: sqlite3.Connection, business_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM categories WHERE business_id = ? AND kind = 'transfer' AND active = 1 ORDER BY code LIMIT 1",
        (business_id,),
    ).fetchone()
    if row is None:
        raise BooksError("this business has no active transfer category")
    return row
