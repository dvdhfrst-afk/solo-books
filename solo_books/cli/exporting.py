"""Exports: the books as journals for other accounting programs, and the whole books as one archive file."""
from __future__ import annotations

import os
from pathlib import Path

from .. import archive, db, journal
from ..journal.entries import UNCATEGORIZED
from ..periods import parse_period
from ..reports import NOT_ADVICE
from .common import add_command, add_group, chosen_businesses, deliver

PERIOD_HELP = "2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20"


def cmd_export_journal(args, conn) -> int:
    period = parse_period(args.period)
    books_journal = journal.build_journal(conn, chosen_businesses(conn, args.business), period)
    writer = journal.FORMATS[args.format]
    deliver(writer.render(books_journal), args.output, writer.BYTE_ORDER_MARK)
    rows = [entry for entry in books_journal.entries if entry.transaction_id is not None]
    waiting = sum(1 for entry in rows if entry.status == "waiting")
    print(f"{len(rows)} row(s) and {len(books_journal.accounts)} accounts, {period.label}.")
    if waiting:
        print(f"WARNING: {waiting} row(s) waiting for review are booked to {UNCATEGORIZED}. Run 'review' first.")
    print(NOT_ADVICE)
    return 0


def cmd_export_archive(args, conn) -> int:
    text, counts = archive.export_archive(conn)
    deliver(text, args.output, byte_order_mark=False)
    print(
        f"{counts['businesses']} business(es), {counts['accounts']} account(s), {counts['transactions']} row(s), "
        f"{counts['reconciliations']} reconciliation(s) and {counts['imports']} import(s), archive version "
        f"{archive.current_version()}."
    )
    print("Load it into new books with: import-archive FILE --db NEW-BOOKS")
    return 0


def cmd_import_archive(args) -> int:
    path = db.resolve_db_path(args.db)
    created = not path.exists()
    conn = db.connect(path, create=True)
    try:
        counts, read_as = archive.import_archive(conn, Path(args.file))
    except BaseException:
        conn.close()
        if created and path.exists():
            # Books made only to receive the archive should not stay behind empty.
            os.unlink(path)
        raise
    conn.close()
    upgraded = f", upgraded from archive version {read_as}" if read_as != archive.current_version() else ""
    print(
        f"Loaded {counts['businesses']} business(es), {counts['accounts']} account(s), {counts['transactions']} row(s), "
        f"{counts['reconciliations']} reconciliation(s) and {counts['imports']} import(s) into {path}{upgraded}."
    )
    print("Its SHA-256 matched and every field was checked. Run 'check' to look over the books.")
    return 0


def register(commands) -> None:
    export = add_group(commands, "export", "the books as a journal for other programs, or as one archive file")
    p = add_command(export, "journal", cmd_export_journal, "write the books as a Ledger, Beancount, QIF or CSV journal")
    p.add_argument("--format", required=True, choices=journal.names(), help=journal.help_text())
    p.add_argument("--period", required=True, help=PERIOD_HELP)
    p.add_argument("--business", help="default: every business")
    p.add_argument("-o", "--output", required=True, help="file to write")
    p = add_command(export, "archive", cmd_export_archive, "write the whole books as one JSON file, with a SHA-256 of its content")
    p.add_argument("-o", "--output", required=True, help="file to write")
    p = add_command(
        commands, "import-archive", cmd_import_archive, "load an archive from 'export archive' into new, empty books",
        needs_conn=False,
    )
    p.add_argument("file", help="the archive file")
