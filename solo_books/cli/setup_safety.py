"""Setting up the books and keeping them safe: init, status, backup, check, restore, salvage."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .. import books, db, ledger, render, review, safety
from .common import EXIT_FINDING, EXIT_OK, add_command, program_name


def cmd_init(args) -> int:
    path = db.resolve_db_path(args.db)
    if path.exists() and path.is_file() and path.stat().st_size > 0:
        db.connect(path).close()
        print(f"Books already exist at {path}. Nothing changed.")
        return 0
    db.connect(path, create=True).close()
    print(f"Created empty books at {path}.")
    print(f"Next: {program_name()} business add \"Your Business\"")
    return 0


def cmd_status(args, conn) -> int:
    print(f"Books: {db.resolve_db_path(args.db)}")
    print(f"Last backup: {db.get_meta(conn, 'last_backup_at') or 'never. Make one with the backup command.'}")
    if not books.list_businesses(conn):
        print(f"\nNo businesses yet. Next: {program_name()} business add \"Your Business\"")
        return 0
    if not books.list_accounts(conn):
        print(f"\nNo accounts yet. Next: {program_name()} account add --help")
        return 0
    rows = []
    for business in books.list_businesses(conn):
        waiting = len(review.queue(conn, business["id"]))
        for account in books.list_accounts(conn, business["id"]):
            summary = ledger.account_summary(conn, account)
            rows.append(
                [
                    business["name"],
                    account["name"],
                    str(summary["rows"]),
                    summary["latest"] or "-",
                    summary["reconciled_through"] or "never",
                ]
            )
        print(f"{business['name']}: {waiting} row(s) waiting for review")
    print()
    print(render.table(["Business", "Account", "Rows", "Latest row", "Reconciled through"], rows, {2}))
    return 0


def cmd_backup(args, conn) -> int:
    books_path = db.resolve_db_path(args.db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = Path(args.file) if args.file else books_path.with_name(f"{books_path.stem}-backup-{stamp}{books_path.suffix}")
    db.backup(conn, target)
    rows = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"Backed up {rows} row(s) to {target}. It passed SQLite's integrity check.")
    return 0


def _print_check(report: safety.CheckReport) -> None:
    print(f"Checked {report.path}")
    if report.version is not None:
        print(f"  Schema version {report.version}; this program reads up to {db.schema_version()}.")
    if report.counts:
        counts = report.counts
        print(
            f"  {counts.get('businesses', 0)} business(es), {counts.get('accounts', 0)} account(s), "
            f"{counts.get('rows', 0)} row(s), {counts.get('waiting', 0)} waiting for review, "
            f"{counts.get('rules', 0)} rule(s), {counts.get('finished', 0)} finished reconciliation(s)."
        )
    for note in report.notes:
        print(f"  NOTE: {note}")
    for problem in report.problems:
        print(f"  PROBLEM: {problem}")


def cmd_check(args) -> int:
    report = safety.check_books(db.resolve_db_path(args.db))
    _print_check(report)
    if report.healthy:
        print("RESULT: no problems found.")
        return EXIT_OK
    print("RESULT: PROBLEMS FOUND. Nothing was changed.")
    print(f"Next: keep a copy of this file as it is, then follow {db.RECOVERY_DOC}.")
    print(f"In short: '{program_name()} restore BACKUP' if you have a backup, or '{program_name()} salvage NEW-FILE' if you do not.")
    return EXIT_FINDING


def cmd_restore(args) -> int:
    books_path = db.resolve_db_path(args.db)
    kept = safety.restore(books_path, Path(args.file))
    print(f"Restored {args.file} to {books_path}.")
    if kept:
        print(f"The books as they were are kept at {kept}.")
    print("Anything imported since that backup is gone from the books. Import those files again; rows already there are not doubled.")
    return EXIT_OK


def cmd_salvage(args) -> int:
    source, destination = db.resolve_db_path(args.db), Path(args.file)
    outcome = safety.salvage(source, destination)
    print(f"Copied what could be read from {source} to {destination}. {source} was only read.")
    for table, count in sorted(outcome.copied.items()):
        print(f"  {table}: {count} row(s) copied")
    if outcome.cut_short:
        print("  The file was cut short, as by an interrupted copy. Whatever was in the missing end is gone.")
    for table, count in sorted(outcome.unreadable.items()):
        print(f"  {table}: up to {count} row(s) could not be read")
    for table in sorted(outcome.unknown_loss):
        print(f"  {table}: part of this table could not be read, and the file cannot say how many rows that was")
    for table, count in sorted(outcome.refused.items()):
        print(f"  {table}: {count} row(s) were too damaged to keep")
    if outcome.detached:
        print(f"  {outcome.detached} link(s) to something lost were cleared. Rows whose category was lost are back in the review queue.")
    if outcome.removed:
        print(f"  {outcome.removed} row(s) were dropped because what they belong to was lost.")
    report = safety.check_books(destination)
    _print_check(report)
    print("Now use the copy with --db, import your bank files again (rows already there are not doubled), and reconcile.")
    return EXIT_OK if report.healthy else EXIT_FINDING


def register(commands) -> None:
    add_command(commands, "init", cmd_init, "create empty books", needs_conn=False)
    add_command(commands, "status", cmd_status, "what is waiting, and how far each account is reconciled")
    p = add_command(commands, "backup", cmd_backup, "write a checked copy of the books to a new file")
    p.add_argument("file", nargs="?", help="default: a dated name beside the books")
    add_command(commands, "check", cmd_check, "check the books for damage and broken bookkeeping; exit status 1 if anything is wrong", needs_conn=False)
    p = add_command(commands, "restore", cmd_restore, "put a backup in place of the books; the books as they were are kept", needs_conn=False)
    p.add_argument("file", help="the backup to restore")
    p = add_command(commands, "salvage", cmd_salvage, "copy every readable row from damaged books into a new file", needs_conn=False)
    p.add_argument("file", help="the new file to write")
