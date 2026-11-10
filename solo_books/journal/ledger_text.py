"""The plain-text journal that Ledger and hledger read.

Ledger has no way to escape text, so anything that would change how a line reads is replaced instead: a semicolon
would start a comment, two spaces would end an account name, and a colon inside a name would split it.

A payee with a name of its own heads the transaction, and the bank's text follows on a comment line. Tags go on a
Ledger tag line, :Spring:Harbor_Lane:, with spaces as underscores and colons left out.
"""
from __future__ import annotations

import re
from typing import List

from ..money import plain_cents
from .entries import Journal, one_line, unique_names

NAME = "ledger"
HELP = "a Ledger or hledger journal"
BYTE_ORDER_MARK = False

_NOT_IN_NAMES = re.compile(r"[:;()\[\]{}\t]")
_FLAGS = {"reconciled": "* ", "waiting": "! ", "": ""}


def component(text: str) -> str:
    return one_line(_NOT_IN_NAMES.sub(" ", text)) or "Unnamed"


def payee(text: str) -> str:
    return one_line(text.replace(";", ",")) or "No description"


def tag_line(names: List[str]) -> str:
    parts = [part for part in (one_line(name.replace(":", " ")).replace(" ", "_") for name in names) if part]
    return f":{':'.join(parts)}:" if parts else ""


def amount(cents: int) -> str:
    return f"${plain_cents(cents)}"


def render(journal: Journal) -> str:
    names = unique_names(journal.accounts, component)
    width = max([len(name) for name in names.values()] + [0])
    lines: List[str] = [f"; {line}" for line in journal.header_lines()]
    lines.append("")
    lines.extend(f"account {names[account]}" for account in journal.accounts)
    for entry in journal.entries:
        code = f"({entry.transaction_id}) " if entry.transaction_id is not None else ""
        lines.append("")
        heading = entry.payee if entry.named else entry.description
        lines.append(f"{entry.day.strftime('%Y/%m/%d')} {_FLAGS[entry.status]}{code}{payee(heading)}")
        if entry.named:
            lines.append(f"    ; Bank text: {one_line(entry.description)}")
        if tag_line(entry.tags):
            lines.append(f"    ; {tag_line(entry.tags)}")
        if entry.memo:
            lines.append(f"    ; Memo: {one_line(entry.memo)}")
        if entry.note:
            lines.append(f"    ; Note: {one_line(entry.note)}")
        for account, cents in entry.postings:
            lines.append(f"    {names[account]:<{width}}  {amount(cents)}")
    return "\n".join(lines) + "\n"
