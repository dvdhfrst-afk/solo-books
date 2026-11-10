"""Quicken Interchange Format, which many personal and small-business programs import.

QIF is single-entry: each record is one amount on one account, with a category. A category of a business is written
"Business:Category", and the clearing and uncategorized accounts are written in brackets, which QIF reads as a move
to another account. QIF has no comments, so what produced the file is said in each account's description line.
"""
from __future__ import annotations

import re
from typing import Dict, List

from ..money import plain_cents
from .entries import Entry, Journal, JournalAccount, one_line

NAME = "qif"
HELP = "Quicken Interchange Format, one block per account"
BYTE_ORDER_MARK = False

# A colon starts a subcategory, a slash a class, and brackets mean another account.
_NOT_IN_NAMES = re.compile(r"[:/\[\]]")
_ACCOUNT_TYPES = {"credit_card": "CCard"}


def _name(text: str) -> str:
    return one_line(_NOT_IN_NAMES.sub("-", text)) or "Unnamed"


def category(account: JournalAccount) -> str:
    """What goes after L: a category, or another account in brackets."""
    if account.role == "category":
        return f"{_name(account.business)}:{_name(account.leaf)}"
    return f"[{_name(account.business)} {_name(account.leaf)}]"


def _record(entry: Entry) -> List[str]:
    lines = [f"D{entry.day.strftime('%m/%d/%Y')}", f"T{plain_cents(entry.amount)}"]
    if entry.status == "reconciled":
        lines.append("CX")
    lines.append(f"P{one_line(entry.description)}")
    memo = "; ".join(one_line(text) for text in (entry.memo, entry.note) if text)
    if memo:
        lines.append(f"M{memo}")
    if entry.other.role == "opening":
        # Quicken's own convention: an opening balance is a move from the account to itself.
        lines.append(f"L[{_name(entry.bank.leaf)}]")
    else:
        lines.append(f"L{category(entry.other)}")
    lines.append("^")
    return lines


def render(journal: Journal) -> str:
    about = " ".join(journal.header_lines()[:2])
    lines: List[str] = ["!Type:Cat"]
    for account in journal.accounts:
        if account.role == "category":
            kind = "I" if account.kind in ("income", "returns") else "E"
            lines.extend([f"N{category(account)}", f"D{account.hint} {_name(account.leaf)}", kind, "^"])
    by_bank: Dict[JournalAccount, List[Entry]] = {bank: [] for bank in journal.banks}
    for entry in journal.entries:
        by_bank[entry.bank].append(entry)
    for bank, entries in by_bank.items():
        lines.extend([
            "!Account",
            f"N{_name(bank.leaf)}",
            f"T{_ACCOUNT_TYPES.get(bank.kind, 'Bank')}",
            f"D{_name(bank.business)}. {about}",
            "^",
            f"!Type:{_ACCOUNT_TYPES.get(bank.kind, 'Bank')}",
        ])
        for entry in entries:
            lines.extend(_record(entry))
    return "\n".join(lines) + "\n"
