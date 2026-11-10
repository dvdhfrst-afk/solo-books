"""Single rows: finding, showing, adding, correcting, deleting and noting them."""
from __future__ import annotations

from .. import books, ledger, render, schedule_c
from ..errors import BooksError
from ..money import format_cents, parse_cents
from ..periods import parse_iso_date, parse_period
from .common import add_command, add_group, deliver, positive_arg, scope_ids


TXN_HEADERS = ["Id", "Date", "Account", "Amount", "Description", "Category", "By", "Rec"]


def cmd_txn_list(args, conn) -> int:
    business_id, account_id = scope_ids(conn, args)
    category_id = None
    if args.category:
        if business_id is None:
            business_id = books.get_business(conn, None)["id"]
        category_id = books.find_category(conn, business_id, args.category)["id"]
    rows = ledger.find_transactions(
        conn,
        business_id=business_id,
        account_id=account_id,
        period=parse_period(args.period) if args.period else None,
        category_id=category_id,
        uncategorized=args.uncategorized,
        search=args.search,
        limit=args.limit,
    )
    if args.format == "csv":
        form = schedule_c.latest_form()
        deliver(render.csv_text(render.DETAIL_CSV_HEADERS, render.detail_csv_rows(rows, form)), args.output)
        return 0
    table_rows = [
        [
            str(r["id"]),
            r["posted_on"],
            r["account_name"],
            format_cents(r["amount_cents"]),
            r["description"],
            f"{r['category_code']} {r['category_name']}" if r["category_id"] else "-",
            r["category_source"] or "",
            "yes" if r["reconciliation_id"] else "",
        ]
        for r in rows
    ]
    print(render.table(TXN_HEADERS, table_rows, {0, 3}))
    print(f"{len(rows)} row(s), net {format_cents(sum(r['amount_cents'] for r in rows))}.")
    return 0


def _booked_by(conn, row) -> str:
    if row["category_id"] is None:
        return "waiting for review"
    booked = f"{row['category_code']} {row['category_name']}"
    if row["category_source"] == "manual":
        return f"{booked}, by hand"
    rule = None
    if row["rule_id"] is not None:
        rule = conn.execute("SELECT match_type, pattern FROM rules WHERE id = ?", (row["rule_id"],)).fetchone()
    if rule is None:
        return f"{booked}, by a rule since deleted"
    return f"{booked}, by rule {row['rule_id']} ({rule['match_type']} {rule['pattern']!r})"


def cmd_txn_show(args, conn) -> int:
    row = ledger.get_transaction(conn, args.id)
    print(f"Transaction {row['id']}")
    print(f"  Date:        {row['posted_on']}")
    print(f"  Account:     {row['account_name']} ({row['business_name']})")
    print(f"  Amount:      {format_cents(row['amount_cents'])}")
    print(f"  Description: {row['description']}")
    print(f"  Merchant:    {row['merchant']}")
    if row["memo"]:
        print(f"  Memo:        {row['memo']}")
    if row["reference"]:
        print(f"  Bank id:     {row['reference']}")
    print(f"  Category:    {_booked_by(conn, row)}")
    held_back = ledger.rules_holding_back(conn, row["id"])
    if held_back:
        print(f"  Held back from rule(s): {', '.join(str(rule_id) for rule_id in held_back)}")
    if row["reconciliation_id"] is not None:
        print(f"  Reconciled:  finished reconciliation {row['reconciliation_id']}")
    else:
        print("  Reconciled:  no")
    sources = ledger.live_imports_holding(conn, row["id"])
    if sources:
        print("  Imported:    " + ", ".join(f"{source['file_name']} (import {source['id']})" for source in sources))
    else:
        print("  Imported:    no, added by hand")
    if row["note"]:
        print(f"  Note:        {row['note']}")
    return 0


def cmd_txn_add(args, conn) -> int:
    account = books.get_account(conn, args.account)
    category = books.find_category(conn, account["business_id"], args.category) if args.category else None
    transaction_id = ledger.add_manual(
        conn, account, parse_iso_date(args.date), parse_cents(args.amount), args.description, category, args.note or ""
    )
    print(f"Added transaction {transaction_id}.")
    finished = ledger.finished_reconciliation_on(conn, account["id"], parse_iso_date(args.date))
    if finished:
        print(f"WARNING: this date is inside finished reconciliation {finished}, so that period no longer ties.")
    return 0


def cmd_txn_edit(args, conn) -> int:
    if args.date is None and args.amount is None and args.description is None:
        raise BooksError("nothing to change. Pass --date, --amount or --description.")
    ledger.edit_manual(
        conn,
        args.id,
        parse_iso_date(args.date) if args.date else None,
        parse_cents(args.amount) if args.amount is not None else None,
        args.description,
    )
    print(f"Transaction {args.id} changed.")
    return 0


def cmd_txn_delete(args, conn) -> int:
    for transaction_id in args.ids:
        ledger.delete_transaction(conn, transaction_id)
        print(f"Deleted transaction {transaction_id}.")
    return 0


def cmd_txn_note(args, conn) -> int:
    ledger.set_note(conn, args.id, args.note)
    print(f"Note saved on transaction {args.id}.")
    return 0


def register(commands) -> None:
    txn = add_group(commands, "txn", "individual rows")
    p = add_command(txn, "list", cmd_txn_list, "find rows")
    p.add_argument("--business")
    p.add_argument("--account")
    p.add_argument("--period", help="2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20")
    p.add_argument("--category")
    p.add_argument("--uncategorized", action="store_true")
    p.add_argument("--search")
    p.add_argument("--limit", type=positive_arg)
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(txn, "show", cmd_txn_show, "everything about one row: how it was booked, where it came from, its note")
    p.add_argument("id", type=int)
    p = add_command(txn, "add", cmd_txn_add, "add a row by hand")
    p.add_argument("account")
    p.add_argument("--date", required=True)
    p.add_argument("--amount", required=True, help="signed: negative is money out")
    p.add_argument("--description", required=True)
    p.add_argument("--category")
    p.add_argument("--note")
    p = add_command(txn, "edit", cmd_txn_edit, "correct a row you added by hand; rows from a bank file stay as written")
    p.add_argument("id", type=int)
    p.add_argument("--date")
    p.add_argument("--amount", help="signed: negative is money out")
    p.add_argument("--description")
    p = add_command(txn, "delete", cmd_txn_delete, "delete rows; later imports will not bring them back")
    p.add_argument("ids", nargs="+", type=int)
    p = add_command(txn, "note", cmd_txn_note, "attach a note to a row")
    p.add_argument("id", type=int)
    p.add_argument("note")
