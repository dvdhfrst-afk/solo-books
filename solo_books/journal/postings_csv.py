"""The journal as CSV: one row per posting, two per transaction, each pair adding to zero.

Like the other CSV files this program writes, what produced it and the not-advice line are rows of their own at the end.
The payee column holds the payee's name in the directory, and the tags column every tag, separated by "; ".
"""
from __future__ import annotations

from typing import List

from ..money import plain_cents
from ..render import csv_text
from ..text import spreadsheet_safe
from .entries import Journal, one_line, unique_names

NAME = "csv"
HELP = "one CSV row per posting"
BYTE_ORDER_MARK = True

HEADERS = [
    "entry", "date", "transaction_id", "status", "business", "description", "memo", "note", "account", "amount", "payee", "tags",
]


def render(journal: Journal) -> str:
    names = unique_names(journal.accounts, one_line)
    rows: List[List[str]] = []
    for number, entry in enumerate(journal.entries, start=1):
        for account, cents in entry.postings:
            rows.append([
                str(number),
                entry.day.isoformat(),
                "" if entry.transaction_id is None else str(entry.transaction_id),
                entry.status,
                entry.bank.business,
                spreadsheet_safe(one_line(entry.description)),
                spreadsheet_safe(one_line(entry.memo)),
                spreadsheet_safe(one_line(entry.note)),
                names[account],
                plain_cents(cents),
                spreadsheet_safe(one_line(entry.payee)),
                spreadsheet_safe("; ".join(entry.tags)),
            ])
    for line in journal.header_lines():
        rows.append(["", "", "", "note", "", line, "", "", "", "", "", ""])
    return csv_text(HEADERS, rows)
