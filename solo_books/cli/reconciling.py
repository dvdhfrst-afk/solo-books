"""Tying an account to a statement, and finishing, reopening and listing reconciliations."""
from __future__ import annotations

from pathlib import Path

from .. import books, reconcile, render
from ..money import format_cents, parse_cents
from ..periods import parse_iso_date
from .common import add_command, add_group


def cmd_reconcile_run(args, conn) -> int:
    account = books.get_account(conn, args.account)
    report = reconcile.run(
        conn,
        account,
        parse_iso_date(args.start),
        parse_iso_date(args.end),
        parse_cents(args.begin),
        parse_cents(args.finish),
        Path(args.statement) if args.statement else None,
        args.skip_bad,
    )
    print(render.reconcile_text(report, args.settings.show_date))
    return 0 if report.balanced else 1


def cmd_reconcile_show(args, conn) -> int:
    report = reconcile.compute(
        conn, reconcile.get(conn, args.id), Path(args.statement) if args.statement else None, args.skip_bad
    )
    print(render.reconcile_text(report, args.settings.show_date))
    return 0 if report.balanced else 1


def cmd_reconcile_finish(args, conn) -> int:
    report = reconcile.finish(conn, args.id)
    print(
        f"Reconciliation {args.id} finished: {report.account['name']}, {args.settings.show_date(report.start)} to "
        f"{args.settings.show_date(report.end)}, "
        f"{report.row_count} rows locked."
    )
    return 0


def cmd_reconcile_reopen(args, conn) -> int:
    reconcile.reopen(conn, args.id)
    print(f"Reconciliation {args.id} is open again and its rows are unlocked.")
    return 0


def cmd_reconcile_forget(args, conn) -> int:
    reconcile.forget(conn, args.id)
    print(f"Reconciliation {args.id} removed.")
    return 0


def cmd_reconcile_list(args, conn) -> int:
    account_id = books.get_account(conn, args.account)["id"] if args.account else None
    rows = []
    for recon in reconcile.list_for_account(conn, account_id):
        report = reconcile.compute(conn, recon)
        rows.append(
            [
                str(recon["id"]),
                recon["account_name"],
                args.settings.show_date(recon["period_start"]),
                args.settings.show_date(recon["period_end"]),
                reconcile.printed(report.account, recon["statement_begin_cents"]),
                reconcile.printed(report.account, recon["statement_end_cents"]),
                format_cents(report.difference),
                recon["status"],
            ]
        )
    print(render.table(["Id", "Account", "From", "To", "Begin", "End", "Diff", "Status"], rows, {0, 4, 5, 6}))
    return 0


def register(commands) -> None:
    rec = add_group(commands, "reconcile", "tie an account to a statement")
    p = add_command(rec, "run", cmd_reconcile_run, "compare the books to a statement period; exit status 1 if they differ")
    p.add_argument("account")
    p.add_argument("--from", dest="start", required=True, help="first day of the statement, YYYY-MM-DD")
    p.add_argument("--to", dest="end", required=True, help="last day of the statement, YYYY-MM-DD")
    p.add_argument("--begin", required=True, help="beginning balance as printed; on a card, what was owed")
    p.add_argument("--end", dest="finish", required=True, help="ending balance as printed; on a card, what was owed")
    p.add_argument("--statement", help="CSV of the statement's lines, compared line by line")
    p.add_argument("--skip-bad", action="store_true", help="compare the readable statement rows even if some are not")
    p = add_command(rec, "show", cmd_reconcile_show, "show a reconciliation again")
    p.add_argument("id", type=int)
    p.add_argument("--statement", help="CSV of the statement's lines, compared line by line")
    p.add_argument("--skip-bad", action="store_true", help="compare the readable statement rows even if some are not")
    p = add_command(rec, "finish", cmd_reconcile_finish, "lock a period that ties to the cent")
    p.add_argument("id", type=int)
    p = add_command(rec, "reopen", cmd_reconcile_reopen, "unlock the latest finished period")
    p.add_argument("id", type=int)
    p = add_command(rec, "forget", cmd_reconcile_forget, "remove an open reconciliation")
    p.add_argument("id", type=int)
    p = add_command(rec, "list", cmd_reconcile_list, "list reconciliations")
    p.add_argument("--account")
