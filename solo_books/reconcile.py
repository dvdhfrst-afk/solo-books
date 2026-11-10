"""Tying an account to a bank statement for a period, to the cent, and showing what does not match."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from . import history
from .books import book_to_printed, printed_to_book
from .csvmap import ParsedRow
from .db import transaction
from .errors import BooksError
from .importer import read_export
from .ledger import balance_through, find_transactions
from .money import format_cents
from .periods import Period, parse_iso_date
from .text import merchant_key

_NEAR_DAYS = 5


@dataclass
class ReconcileReport:
    reconciliation: sqlite3.Row
    account: sqlite3.Row
    start: date
    end: date
    statement_begin: int
    statement_end: int
    activity: int
    money_in: int
    money_out: int
    row_count: int
    expected_end: int
    difference: int
    book_begin: Optional[int] = None
    begin_gap: Optional[int] = None
    prior_id: Optional[int] = None
    prior_gap: Optional[int] = None
    notes: List[str] = field(default_factory=list)
    hints: List[str] = field(default_factory=list)
    compared_statement: bool = False
    statement_rows_outside: int = 0
    statement_only: List[ParsedRow] = field(default_factory=list)
    book_only: List[sqlite3.Row] = field(default_factory=list)
    date_differs: List[Tuple[ParsedRow, sqlite3.Row]] = field(default_factory=list)
    amount_differs: List[Tuple[ParsedRow, sqlite3.Row]] = field(default_factory=list)
    rows_added_after_finish: List[sqlite3.Row] = field(default_factory=list)

    @property
    def balances_tie(self) -> bool:
        return self.difference == 0 and not self.begin_gap and not self.prior_gap

    @property
    def balanced(self) -> bool:
        return (
            self.balances_tie
            and not self.statement_only
            and not self.book_only
            and not self.amount_differs
        )


def _get(conn: sqlite3.Connection, reconciliation_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM reconciliations WHERE id = ?", (reconciliation_id,)).fetchone()
    if row is None:
        raise BooksError(f"no reconciliation {reconciliation_id}")
    return row


def _account_of(conn: sqlite3.Connection, recon: sqlite3.Row) -> sqlite3.Row:
    return conn.execute("SELECT * FROM accounts WHERE id = ?", (recon["account_id"],)).fetchone()


def run(
    conn: sqlite3.Connection,
    account: sqlite3.Row,
    start: date,
    end: date,
    printed_begin: int,
    printed_end: int,
    statement_file: Optional[Path] = None,
    skip_bad: bool = False,
) -> ReconcileReport:
    """Record the statement's figures for a period and compare the books to them.

    Balances are entered as the statement prints them; on a card that means what is owed.
    Running it again for the same period replaces the figures of an open reconciliation.
    """
    if end < start:
        raise BooksError("the statement period ends before it starts")
    overlap = conn.execute(
        "SELECT * FROM reconciliations WHERE account_id = ? AND status = 'finished'"
        " AND period_start <= ? AND period_end >= ?",
        (account["id"], end.isoformat(), start.isoformat()),
    ).fetchone()
    if overlap:
        raise BooksError(
            f"this period overlaps finished reconciliation {overlap['id']} "
            f"({overlap['period_start']} to {overlap['period_end']}). Reopen it to change it."
        )
    begin, ending = printed_to_book(account, printed_begin), printed_to_book(account, printed_end)
    find = "SELECT * FROM reconciliations WHERE account_id = ? AND period_start = ? AND period_end = ?"
    key = (account["id"], start.isoformat(), end.isoformat())
    earlier = conn.execute(find, key).fetchone()
    with transaction(conn):
        conn.execute(
            "INSERT INTO reconciliations (account_id, period_start, period_end, statement_begin_cents, statement_end_cents)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (account_id, period_start, period_end) DO UPDATE SET"
            " statement_begin_cents = excluded.statement_begin_cents, statement_end_cents = excluded.statement_end_cents",
            (*key, begin, ending),
        )
        recon = conn.execute(find, key).fetchone()
        unchanged = earlier is not None and (earlier["statement_begin_cents"], earlier["statement_end_cents"]) == (begin, ending)
        if not unchanged:
            history.record(
                conn, "reconcile-run",
                f"Recorded statement figures for {account['name']}, {start} to {end} (reconciliation {recon['id']}).",
                account["business_id"],
                {
                    "reconciliation_id": recon["id"], "account": account["name"], "from": start.isoformat(), "to": end.isoformat(),
                    "before": {"begin_cents": earlier["statement_begin_cents"], "end_cents": earlier["statement_end_cents"]} if earlier else None,
                    "after": {"begin_cents": begin, "end_cents": ending},
                },
            )
    return compute(conn, recon, statement_file, skip_bad)


def compute(
    conn: sqlite3.Connection, recon: sqlite3.Row, statement_file: Optional[Path] = None, skip_bad: bool = False
) -> ReconcileReport:
    account = _account_of(conn, recon)
    start, end = parse_iso_date(recon["period_start"]), parse_iso_date(recon["period_end"])
    rows = find_transactions(conn, account_id=account["id"], period=Period(start, end, ""))
    activity = sum(row["amount_cents"] for row in rows)
    begin, ending = recon["statement_begin_cents"], recon["statement_end_cents"]
    expected = begin + activity
    report = ReconcileReport(
        reconciliation=recon,
        account=account,
        start=start,
        end=end,
        statement_begin=begin,
        statement_end=ending,
        activity=activity,
        money_in=sum(row["amount_cents"] for row in rows if row["amount_cents"] > 0),
        money_out=-sum(row["amount_cents"] for row in rows if row["amount_cents"] < 0),
        row_count=len(rows),
        expected_end=expected,
        difference=ending - expected,
    )
    _check_beginning(conn, account, report)
    if report.difference != 0:
        _explain_difference(conn, account, report, rows, statement_file is not None)
    if statement_file is not None:
        _compare_statement(account, report, rows, Path(statement_file), skip_bad)
    if recon["status"] == "finished":
        report.rows_added_after_finish = [row for row in rows if row["reconciliation_id"] is None]
    return report


def _check_beginning(conn: sqlite3.Connection, account: sqlite3.Row, report: ReconcileReport) -> None:
    start = report.start
    if account["opening_date"] is not None and start.isoformat() >= account["opening_date"]:
        book_begin = balance_through(conn, account, start - timedelta(days=1))
        report.book_begin = book_begin
        report.begin_gap = report.statement_begin - book_begin
        if report.begin_gap:
            report.hints.append(
                "The statement's beginning balance differs from the books before this period, "
                "so earlier rows or the opening balance are off. Check earlier statements first."
            )
    elif account["opening_date"] is None:
        report.notes.append(
            "This account has no opening balance, so only this period's activity is checked. "
            "Set one with 'account opening' to check the beginning balance too."
        )

    prior = conn.execute(
        "SELECT * FROM reconciliations WHERE account_id = ? AND status = 'finished' AND period_end < ?"
        " ORDER BY period_end DESC LIMIT 1",
        (account["id"], start.isoformat()),
    ).fetchone()
    if prior is None:
        return
    report.prior_id = prior["id"]
    if prior["period_end"] == (start - timedelta(days=1)).isoformat():
        report.prior_gap = report.statement_begin - prior["statement_end_cents"]
        if report.prior_gap:
            report.hints.append(
                f"The beginning balance does not equal the ending balance of finished reconciliation {prior['id']}. "
                "Check the figure you typed, or whether the bank restated a balance."
            )
    else:
        report.notes.append(
            f"Dates between {prior['period_end']} (end of reconciliation {prior['id']}) and {start} "
            "have not been reconciled."
        )


def _explain_difference(
    conn: sqlite3.Connection,
    account: sqlite3.Row,
    report: ReconcileReport,
    rows: List[sqlite3.Row],
    has_statement_file: bool,
) -> None:
    """Look for single rows that would explain the whole difference. Suggestions, never changes."""
    diff = report.difference
    found = False
    same_day: dict = {}
    for row in rows:
        marker = (row["posted_on"], row["amount_cents"], row["merchant"])
        same_day[marker] = same_day.get(marker, 0) + 1

    for row in rows:
        if row["amount_cents"] == -diff:
            found = True
            marker = (row["posted_on"], row["amount_cents"], row["merchant"])
            if same_day[marker] > 1:
                report.hints.append(f"Transaction {_describe(row)} repeats on the same day and equals the difference. It may be a duplicate.")
            else:
                report.hints.append(f"Transaction {_describe(row)} equals the difference. Is it on the statement?")
    if diff % 2 == 0:
        for row in rows:
            if row["amount_cents"] == -diff // 2:
                found = True
                report.hints.append(f"Transaction {_describe(row)} would explain the difference if its sign were reversed.")

    near = Period(report.start - timedelta(days=_NEAR_DAYS), report.end + timedelta(days=_NEAR_DAYS), "")
    for row in find_transactions(conn, account_id=account["id"], period=near):
        if row["amount_cents"] == diff and not (report.start.isoformat() <= row["posted_on"] <= report.end.isoformat()):
            found = True
            report.hints.append(f"Transaction {_describe(row)} is dated just outside the period and equals the difference.")
    if not found:
        advice = f"No single row explains it. Look for a statement line of {format_cents(abs(diff))} that is not in the books"
        report.hints.append(advice + ("." if has_statement_file else ", or compare line by line with --statement FILE."))


def _describe(row: sqlite3.Row) -> str:
    return f"{row['id']} ({row['posted_on']}, {format_cents(row['amount_cents'])}, {row['description']})"


def _compare_statement(
    account: sqlite3.Row, report: ReconcileReport, rows: List[sqlite3.Row], path: Path, skip_bad: bool = False
) -> None:
    """Pair statement lines with book rows: same day and amount, then near day, then changed amount."""
    parsed, _, _ = read_export(account, path)
    if parsed.errors and not skip_bad:
        raise BooksError(
            f"{path.name}: cannot compare, unreadable rows:\n  " + "\n  ".join(parsed.errors[:20])
            + "\nFix the file, or pass --skip-bad to compare the readable rows."
        )
    for error in parsed.errors:
        report.notes.append(f"Statement file, left out of the comparison: {error}")
    report.compared_statement = True
    statement = []
    for line in parsed.rows:
        if report.start <= line.posted_on <= report.end:
            statement.append(line)
        else:
            report.statement_rows_outside += 1

    books = list(rows)

    def take(line: ParsedRow, test) -> Optional[sqlite3.Row]:
        for row in books:
            if test(line, row):
                books.remove(row)
                return row
        return None

    remaining = []
    for line in statement:
        if take(line, lambda s, b: b["posted_on"] == s.posted_on.isoformat() and b["amount_cents"] == s.amount_cents) is None:
            remaining.append(line)

    still = []
    for line in remaining:
        match = take(
            line,
            lambda s, b: b["amount_cents"] == s.amount_cents
            and abs(parse_iso_date(b["posted_on"]).toordinal() - s.posted_on.toordinal()) <= _NEAR_DAYS,
        )
        if match is not None:
            report.date_differs.append((line, match))
        else:
            still.append(line)

    for line in still:
        match = take(
            line,
            lambda s, b: b["posted_on"] == s.posted_on.isoformat() and b["merchant"] == merchant_key(s.description),
        )
        if match is not None:
            report.amount_differs.append((line, match))
        else:
            report.statement_only.append(line)
    report.book_only = books


def finish(conn: sqlite3.Connection, reconciliation_id: int) -> ReconcileReport:
    """Lock a period that ties to the cent. Its rows cannot be deleted until it is reopened."""
    recon = _get(conn, reconciliation_id)
    if recon["status"] == "finished":
        raise BooksError(f"reconciliation {reconciliation_id} is already finished")
    report = compute(conn, recon)
    if not report.balances_tie:
        raise BooksError(
            f"reconciliation {reconciliation_id} does not tie: difference {format_cents(report.difference)}"
            + (f", beginning balance off by {format_cents(report.begin_gap)}" if report.begin_gap else "")
            + (f", beginning differs from the prior ending by {format_cents(report.prior_gap)}" if report.prior_gap else "")
            + ". Fix the books until it ties; see 'reconcile show'."
        )
    period = Period(report.start, report.end, "")
    with transaction(conn):
        conn.execute(
            "UPDATE reconciliations SET status = 'finished', finished_at = datetime('now') WHERE id = ?",
            (reconciliation_id,),
        )
        span = (recon["account_id"], period.start.isoformat(), period.end.isoformat())
        locked = conn.execute(
            "SELECT id, reconciliation_id FROM transactions WHERE account_id = ? AND posted_on BETWEEN ? AND ? ORDER BY id", span
        ).fetchall()
        conn.execute(
            "UPDATE transactions SET reconciliation_id = ? WHERE account_id = ? AND posted_on BETWEEN ? AND ?",
            (reconciliation_id, *span),
        )
        _record_lock(conn, "reconcile-finish", report.account, recon, f"Finished reconciliation {reconciliation_id}", [
            (row["id"], {"reconciliation_id": row["reconciliation_id"]}, {"reconciliation_id": reconciliation_id}) for row in locked
        ])
    return compute(conn, _get(conn, reconciliation_id))


def _record_lock(conn: sqlite3.Connection, kind: str, account: sqlite3.Row, recon: sqlite3.Row, what: str, rows: list) -> None:
    history.record(
        conn, kind, f"{what}: {account['name']}, {recon['period_start']} to {recon['period_end']}, {len(rows)} row(s).",
        account["business_id"],
        {
            "reconciliation_id": recon["id"], "account": account["name"], "from": recon["period_start"],
            "to": recon["period_end"], "begin_cents": recon["statement_begin_cents"], "end_cents": recon["statement_end_cents"],
        },
        rows,
    )


def reopen(conn: sqlite3.Connection, reconciliation_id: int) -> None:
    """Unlock a finished period. Only the latest one, so later periods keep a sound starting point."""
    recon = _get(conn, reconciliation_id)
    if recon["status"] != "finished":
        raise BooksError(f"reconciliation {reconciliation_id} is not finished")
    later = conn.execute(
        "SELECT id FROM reconciliations WHERE account_id = ? AND status = 'finished' AND period_start > ?"
        " ORDER BY period_start LIMIT 1",
        (recon["account_id"], recon["period_start"]),
    ).fetchone()
    if later:
        raise BooksError(f"reopen the later reconciliation {later['id']} first")
    with transaction(conn):
        conn.execute(
            "UPDATE reconciliations SET status = 'open', finished_at = NULL WHERE id = ?", (reconciliation_id,)
        )
        unlocked = conn.execute(
            "SELECT id FROM transactions WHERE reconciliation_id = ? ORDER BY id", (reconciliation_id,)
        ).fetchall()
        conn.execute("UPDATE transactions SET reconciliation_id = NULL WHERE reconciliation_id = ?", (reconciliation_id,))
        _record_lock(conn, "reconcile-reopen", _account_of(conn, recon), recon, f"Reopened reconciliation {reconciliation_id}", [
            (row["id"], {"reconciliation_id": reconciliation_id}, {"reconciliation_id": None}) for row in unlocked
        ])


def forget(conn: sqlite3.Connection, reconciliation_id: int) -> None:
    """Throw away an open reconciliation, for example one started with a mistyped date."""
    recon = _get(conn, reconciliation_id)
    if recon["status"] != "open":
        raise BooksError(f"reconciliation {reconciliation_id} is finished; reopen it first")
    with transaction(conn):
        conn.execute("DELETE FROM reconciliations WHERE id = ?", (reconciliation_id,))
        _record_lock(conn, "reconcile-forget", _account_of(conn, recon), recon, f"Removed reconciliation {reconciliation_id}", [])


def get(conn: sqlite3.Connection, reconciliation_id: int) -> sqlite3.Row:
    return _get(conn, reconciliation_id)


def list_for_account(conn: sqlite3.Connection, account_id: Optional[int] = None) -> List[sqlite3.Row]:
    sql = (
        "SELECT r.*, a.name AS account_name, a.kind AS account_kind FROM reconciliations r"
        " JOIN accounts a ON a.id = r.account_id"
    )
    if account_id is None:
        return conn.execute(sql + " ORDER BY a.name, r.period_start").fetchall()
    return conn.execute(sql + " WHERE r.account_id = ? ORDER BY r.period_start", (account_id,)).fetchall()


def printed(account: sqlite3.Row, book_cents: int) -> str:
    return format_cents(book_to_printed(account, book_cents))
