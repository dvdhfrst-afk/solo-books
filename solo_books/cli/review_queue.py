"""The review queue in the terminal, and booking rows by hand."""
from __future__ import annotations

from .. import books, ledger, render, review
from ..errors import BooksError
from ..money import format_cents
from .common import add_command, scope_ids


def cmd_review(args, conn) -> int:
    business_id, account_id = scope_ids(conn, args)
    rows = review.queue(conn, business_id, account_id)
    if args.groups:
        groups = review.group_rows(rows)
        table_rows = [
            [g.business, g.merchant, str(len(g.rows)), format_cents(g.total_cents), ",".join(str(r["id"]) for r in g.rows[:6])]
            for g in groups
        ]
        print(render.table(["Business", "Merchant", "Rows", "Total", "First ids"], table_rows, {2, 3}))
        print(f"{len(rows)} row(s) in {len(groups)} group(s).")
        return 0
    if args.list:
        table_rows = [
            [
                str(r["id"]), args.settings.show_date(r["posted_on"]), r["account_name"], format_cents(r["amount_cents"]),
                r["description"],
            ]
            for r in rows
        ]
        print(render.table(["Id", "Date", "Account", "Amount", "Description"], table_rows, {0, 3}))
        print(f"{len(rows)} row(s) waiting.")
        return 0
    if not rows:
        print("Nothing waiting for review.")
        return 0
    print(f"{len(rows)} row(s) waiting. Type ? for help.")
    totals = review.run_interactive(conn, business_id, account_id, input, print, show_date=args.settings.show_date)
    left = len(review.queue(conn, business_id, account_id))
    print(f"\nBooked {totals['booked']}, skipped {totals['skipped']}, {left} still waiting.")
    return 0


def cmd_categorize(args, conn) -> int:
    if args.clear:
        if args.category and not args.category.isdigit():
            raise BooksError("usage: categorize --clear ID [ID ...]")
        ids = [int(args.category), *args.ids] if args.category else args.ids
        print(f"{ledger.uncategorize(conn, ids)} row(s) back in the queue.")
        return 0
    if not args.ids or not args.category:
        raise BooksError("usage: categorize CATEGORY ID [ID ...]")
    business_id = ledger.get_transaction(conn, args.ids[0])["business_id"]
    category = books.find_category(conn, business_id, args.category)
    print(f"{ledger.categorize(conn, args.ids, category)} row(s) booked to {category['code']} {category['name']}.")
    return 0


def register(commands) -> None:
    p = add_command(commands, "review", cmd_review, "work through rows no rule could book")
    p.add_argument("--business")
    p.add_argument("--account")
    p.add_argument("--list", action="store_true", help="print the queue and exit")
    p.add_argument("--groups", action="store_true", help="print the queue by merchant and exit")

    p = add_command(commands, "categorize", cmd_categorize, "book rows to a category by hand")
    p.add_argument("category", nargs="?", help="category code or name")
    p.add_argument("ids", nargs="*", type=int, help="transaction ids")
    p.add_argument("--clear", action="store_true", help="send the rows back to the queue instead")
