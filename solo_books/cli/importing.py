"""Importing bank and card files, and the record of past imports."""
from __future__ import annotations

from pathlib import Path

from .. import books, importer, render
from ..money import format_cents
from .common import add_command, add_group, program_name

_MAX_ROW_WARNINGS = 10


def _print_import(result: importer.ImportResult) -> None:
    mode = "DRY RUN, nothing saved. " if result.dry_run else ""
    print(f"{mode}{result.file_name} into {result.account}")
    span = f" ({result.earliest} to {result.latest})" if result.earliest else ""
    print(f"  rows read:          {result.rows_read}{span}")
    print(f"  new:                {result.rows_new}")
    print(f"  already in books:   {result.rows_duplicate}")
    if result.rows_deleted_before:
        print(f"  deleted earlier:    {result.rows_deleted_before} (left out; you removed these on purpose)")
    print(f"  skipped:            {result.rows_skipped} (pending, zero-amount or footer rows)")
    if result.rows_bad:
        print(f"  unreadable:         {result.rows_bad}")
    print(f"  booked by rules:    {result.auto_categorized}")
    print(f"  waiting for review: {result.waiting_for_review}")
    for line in result.bad_rows[:20]:
        print(f"  BAD {line}")
    if result.dry_run and result.bad_rows:
        print("A real import stops on these rows and saves nothing, unless you pass --skip-bad.")
    if result.dry_run and result.samples:
        print("How the first rows read (positive is money in):")
        rows = [[str(r.posted_on), format_cents(r.amount_cents), r.description] for r in result.samples]
        print(render.table(["Date", "Amount", "Description"], rows, {1}))
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for warning in result.row_warnings[:_MAX_ROW_WARNINGS]:
        print(f"WARNING: {warning}")
    if len(result.row_warnings) > _MAX_ROW_WARNINGS:
        print(f"WARNING: ...and {len(result.row_warnings) - _MAX_ROW_WARNINGS} more like these.")


def cmd_import(args, conn) -> int:
    account = books.get_account(conn, args.account)
    result = importer.import_export(conn, account, Path(args.file), dry_run=args.dry_run, skip_bad=args.skip_bad)
    _print_import(result)
    if not result.dry_run and result.waiting_for_review:
        print(f"Next: {program_name()} review --groups")
    return 0


def cmd_imports_list(args, conn) -> int:
    account_id = books.get_account(conn, args.account)["id"] if args.account else None
    rows = [
        [
            str(r["id"]),
            r["account_name"],
            r["file_name"],
            r["imported_at"],
            str(r["rows_read"]),
            str(r["rows_new"]),
            str(r["rows_duplicate"]),
            "undone" if r["undone_at"] else "",
        ]
        for r in importer.list_imports(conn, account_id)
    ]
    print(render.table(["Id", "Account", "File", "When (UTC)", "Read", "New", "Dup", ""], rows, {0, 4, 5, 6}))
    return 0


def cmd_imports_undo(args, conn) -> int:
    outcome = importer.undo_import(conn, args.id, force=args.force)
    print(f"Removed {outcome['removed']} row(s) from import {args.id}.")
    if outcome["kept"]:
        others = ", ".join(str(other) for other in outcome["kept_in"])
        print(f"Kept {outcome['kept']} row(s) that import(s) {others} also held. Undo those too to remove them.")
    return 0


def register(commands) -> None:
    p = add_command(commands, "import", cmd_import, "import a bank or card CSV, OFX, QFX or QBO file; safe to repeat with overlapping files")
    p.add_argument("account")
    p.add_argument("file")
    p.add_argument("--dry-run", action="store_true", help="report what would happen and save nothing")
    p.add_argument("--skip-bad", action="store_true", help="import the readable rows even if some are not")

    imports = add_group(commands, "imports", "past imports")
    p = add_command(imports, "list", cmd_imports_list, "list imports")
    p.add_argument("--account")
    p = add_command(imports, "undo", cmd_imports_undo, "take a whole import back out")
    p.add_argument("id", type=int)
    p.add_argument("--force", action="store_true", help="undo even if rows were categorized by hand")
