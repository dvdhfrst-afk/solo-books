"""The books as double-entry transactions: every bank row moves money between its account and what it was booked to.

Each bank or card account becomes an asset or a liability, and each category an income, expense or equity account
named from the chart. Every transfer category of a business shares one clearing account, rows still waiting for
review go to an equity account named for that, and opening balances come from an equity account of their own.
None of those three is income or an expense, so profit in another program ties to profit and loss here.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple

from ..books import list_accounts, list_categories
from ..formats.base import GENERATOR
from ..ledger import balance_through, find_transactions
from ..periods import Period
from ..reports import NOT_ADVICE

ASSETS, LIABILITIES, INCOME, EXPENSES, EQUITY = "Assets", "Liabilities", "Income", "Expenses", "Equity"
TOP_ORDER = (ASSETS, LIABILITIES, EQUITY, INCOME, EXPENSES)
_TOP_OF_KIND = {"income": INCOME, "returns": INCOME, "cogs": EXPENSES, "expense": EXPENSES, "equity": EQUITY}

TRANSFER_CLEARING = "Transfer clearing"
UNCATEGORIZED = "Uncategorized"
OPENING_BALANCES = "Opening balances"
OPENING_DESCRIPTION = "Opening balance"


@dataclass(frozen=True)
class JournalAccount:
    """One account in the journal. hint tells two apart whose names a format would write the same way."""

    top: str
    business: str
    leaf: str
    hint: str
    role: str  # bank, category, transfer, uncategorized or opening
    kind: str = ""  # the bank account's kind, or the category's

    @property
    def sort_key(self) -> tuple:
        return (TOP_ORDER.index(self.top), self.business.lower(), self.leaf.lower(), self.hint)


@dataclass
class Entry:
    """One transaction. The first posting is always the bank or card account; the second is where the money went."""

    day: date
    description: str
    postings: List[Tuple[JournalAccount, int]]
    transaction_id: Optional[int] = None
    memo: str = ""
    note: str = ""
    status: str = ""  # reconciled, waiting, or nothing

    @property
    def bank(self) -> JournalAccount:
        return self.postings[0][0]

    @property
    def other(self) -> JournalAccount:
        return self.postings[1][0]

    @property
    def amount(self) -> int:
        """The money in (positive) or out (negative) of the bank or card account."""
        return self.postings[0][1]


@dataclass
class Journal:
    period: Period
    businesses: List[str]
    entries: List[Entry]
    closing: List[Tuple[JournalAccount, int]] = field(default_factory=list)

    @property
    def accounts(self) -> List[JournalAccount]:
        """Every account a posting or a closing balance names, in chart order."""
        seen = {account for entry in self.entries for account, _ in entry.postings}
        seen.update(account for account, _ in self.closing)
        return sorted(seen, key=lambda account: account.sort_key)

    @property
    def banks(self) -> List[JournalAccount]:
        return [account for account in self.accounts if account.role == "bank"]

    def header_lines(self) -> List[str]:
        period = self.period
        return [
            f"Written by {GENERATOR} for {period.label} ({period.start} to {period.end}): {', '.join(self.businesses)}.",
            NOT_ADVICE,
            "Bank and card accounts are assets and liabilities. Categories are income, expenses and equity, named from the chart.",
            f"Rows waiting for review are booked to {UNCATEGORIZED}, and every transfer category to {TRANSFER_CLEARING}.",
        ]


def build_journal(conn: sqlite3.Connection, businesses: List[sqlite3.Row], period: Period) -> Journal:
    openings: List[Entry] = []
    rows: List[Entry] = []
    closing: List[Tuple[JournalAccount, int]] = []
    for business in businesses:
        name = business["name"]
        clearing = JournalAccount(ASSETS, name, TRANSFER_CLEARING, "", "transfer")
        waiting = JournalAccount(EQUITY, name, UNCATEGORIZED, "", "uncategorized")
        opening = JournalAccount(EQUITY, name, OPENING_BALANCES, "", "opening")
        categories: Dict[int, JournalAccount] = {}
        for category in list_categories(conn, business["id"], include_retired=True):
            if category["kind"] == "transfer":
                categories[category["id"]] = clearing
            else:
                top = _TOP_OF_KIND[category["kind"]]
                categories[category["id"]] = JournalAccount(top, name, category["name"], category["code"], "category", category["kind"])
        banks: Dict[int, JournalAccount] = {}
        for account in list_accounts(conn, business["id"]):
            top = LIABILITIES if account["kind"] == "credit_card" else ASSETS
            bank = JournalAccount(top, name, account["name"], str(account["id"]), "bank", account["kind"])
            banks[account["id"]] = bank
            entry = _opening_entry(conn, account, bank, opening, period)
            if entry is not None:
                openings.append(entry)
            if account["opening_balance_cents"] is not None and account["opening_date"] <= period.end.isoformat():
                closing.append((bank, balance_through(conn, account, period.end)))
        for row in find_transactions(conn, business_id=business["id"], period=period):
            other = waiting if row["category_id"] is None else categories[row["category_id"]]
            status = "waiting" if row["category_id"] is None else "reconciled" if row["reconciliation_id"] else ""
            rows.append(Entry(
                date.fromisoformat(row["posted_on"]),
                row["description"],
                [(banks[row["account_id"]], row["amount_cents"]), (other, -row["amount_cents"])],
                row["id"],
                row["memo"],
                row["note"],
                status,
            ))
    # Opening balances come first on their day. Python's sort keeps the order of equal keys.
    entries = openings + rows
    entries.sort(key=lambda e: (e.day, e.transaction_id is not None, e.transaction_id or 0))
    return Journal(period, [business["name"] for business in businesses], entries, closing)


def _opening_entry(
    conn: sqlite3.Connection, account: sqlite3.Row, bank: JournalAccount, opening: JournalAccount, period: Period
) -> Optional[Entry]:
    """What the account held when the journal starts, so its balance in another program matches the books.

    An account opened during the period starts on its opening date. Rows dated before that are still in the journal,
    because profit and loss counts them, so the opening entry allows for them.
    """
    if account["opening_balance_cents"] is None or account["opening_date"] > period.end.isoformat():
        return None
    opened_on = date.fromisoformat(account["opening_date"])
    if opened_on <= period.start:
        day = period.start
        cents = balance_through(conn, account, period.start - timedelta(days=1))
    else:
        day = opened_on
        early = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions WHERE account_id = ? AND posted_on >= ? AND posted_on < ?",
            (account["id"], period.start.isoformat(), account["opening_date"]),
        ).fetchone()[0]
        cents = account["opening_balance_cents"] - early
    if cents == 0:
        return None
    return Entry(day, OPENING_DESCRIPTION, [(bank, cents), (opening, -cents)])


def unique_names(accounts: List[JournalAccount], component: Callable[[str], str], separator: str = ":") -> Dict[JournalAccount, str]:
    """Each account's full name as one format writes it, never the same as another's.

    A format may drop characters a name holds, so two names can come out alike. The later one then takes its hint
    (a category code or an account number), and a number after that if it still clashes.
    """
    business_parts: Dict[str, str] = {}
    taken_business = set()
    for account in accounts:
        if account.business in business_parts:
            continue
        part, number = component(account.business), 2
        while part.lower() in taken_business:
            part, number = component(f"{account.business} {number}"), number + 1
        taken_business.add(part.lower())
        business_parts[account.business] = part
    names: Dict[JournalAccount, str] = {}
    taken = set()
    for account in accounts:
        head = [component(account.top), business_parts[account.business]]
        leaf, number = component(account.leaf), 2
        if separator.join(head + [leaf]).lower() in taken and account.hint:
            leaf = component(f"{account.leaf} {account.hint}")
        while separator.join(head + [leaf]).lower() in taken:
            leaf, number = component(f"{account.leaf} {account.hint} {number}"), number + 1
        name = separator.join(head + [leaf])
        taken.add(name.lower())
        names[account] = name
    return names


_SPACES = re.compile(r"\s+")


def one_line(text: str) -> str:
    """Runs of whitespace, line breaks included, become one space."""
    return _SPACES.sub(" ", text).strip()
