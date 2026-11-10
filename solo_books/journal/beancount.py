"""The plain-text journal that Beancount reads.

Beancount account names allow only letters, digits and hyphens, each part starting with a capital or a digit, so
names are reduced to that. Descriptions are quoted strings with backslashes and quotes escaped. Each account opens on
the first day of the journal, and a balance check on the day after it ends holds every account to the books.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import List

from ..money import plain_cents
from .entries import Journal, one_line, unique_names

NAME = "beancount"
HELP = "a Beancount journal, with a balance check per account"
BYTE_ORDER_MARK = False

_NOT_IN_NAMES = re.compile(r"[^A-Za-z0-9]+")


def component(text: str) -> str:
    part = _NOT_IN_NAMES.sub("-", text).strip("-")
    if not part:
        return "X"
    return part[0].upper() + part[1:]


def quoted(text: str) -> str:
    return '"' + one_line(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def amount(cents: int) -> str:
    return f"{plain_cents(cents)} USD"


def render(journal: Journal) -> str:
    names = unique_names(journal.accounts, component)
    width = max([len(name) for name in names.values()] + [0])
    start = journal.period.start.isoformat()
    lines: List[str] = [f"; {line}" for line in journal.header_lines()]
    lines.append("")
    lines.extend(f"{start} open {names[account]} USD" for account in journal.accounts)
    for entry in journal.entries:
        flag = "!" if entry.status == "waiting" else "*"
        lines.append("")
        lines.append(f"{entry.day.isoformat()} {flag} {quoted(entry.description)}")
        if entry.transaction_id is not None:
            lines.append(f"  solo_books_id: {entry.transaction_id}")
        if entry.status == "reconciled":
            lines.append("  reconciled: TRUE")
        if entry.memo:
            lines.append(f"  memo: {quoted(entry.memo)}")
        if entry.note:
            lines.append(f"  note: {quoted(entry.note)}")
        for account, cents in entry.postings:
            lines.append(f"  {names[account]:<{width}}  {amount(cents)}")
    if journal.closing:
        after = (journal.period.end + timedelta(days=1)).isoformat()
        lines.append("")
        lines.extend(f"{after} balance {names[account]:<{width}}  {amount(cents)}" for account, cents in journal.closing)
    return "\n".join(lines) + "\n"
