"""Businesses, their charts of accounts, bank and card accounts, and column mappings."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .. import books, layouts, ledger, render, schedule_c
from ..csvmap import Mapping, suggest_mapping
from ..errors import BooksError
from ..files import write_atomic
from ..money import format_cents, parse_cents
from ..periods import parse_iso_date
from .common import add_command, add_group, program_name, the_business, year_arg


def cmd_business_add(args, conn) -> int:
    business = books.add_business(conn, args.name, args.activity or "")
    count = len(books.list_categories(conn, business["id"]))
    print(f"Added {business['name']} with a starting chart of {count} categories. See 'chart list'.")
    return 0


def cmd_business_list(args, conn) -> int:
    rows = [[b["name"], b["activity"]] for b in books.list_businesses(conn)]
    print(render.table(["Business", "Activity"], rows))
    return 0


def cmd_business_rename(args, conn) -> int:
    business = books.get_business(conn, args.name)
    print(f"{business['name']} is now {books.rename_business(conn, business, args.new_name)}.")
    return 0


def cmd_business_delete(args, conn) -> int:
    business = books.get_business(conn, args.name)
    books.delete_business(conn, business)
    print(f"Deleted {business['name']} and its chart of accounts.")
    return 0


def _current_line(form, category) -> str:
    key = category["schedule_c_key"]
    if not key:
        return ""
    entry = form.find_by_key(key)
    return entry["line"] if entry else f"({key}: not on {form.tax_year})"


def cmd_chart_list(args, conn) -> int:
    business = the_business(conn, args)
    form = schedule_c.load_form(args.year) if args.year else schedule_c.latest_form()
    rows = [
        [c["code"], c["name"], c["kind"], _current_line(form, c), "" if c["active"] else "retired"]
        for c in books.list_categories(conn, business["id"], include_retired=True)
    ]
    print(f"Chart of accounts: {business['name']} (form lines as of tax year {form.tax_year})")
    print(render.table(["Code", "Category", "Kind", "Line", ""], rows))
    accounts = books.list_accounts(conn, business["id"])
    if accounts:
        print("\nBank and card accounts:")
        print(render.table(["Account", "Kind", "Institution"], [[a["name"], a["kind"], a["institution"]] for a in accounts]))
    return 0


def cmd_chart_add(args, conn) -> int:
    business = the_business(conn, args)
    category = books.add_category(conn, business["id"], args.code, args.name, args.kind, args.line, args.year)
    print(f"Added {category['code']} {category['name']} to {business['name']}.")
    return 0


def cmd_chart_map(args, conn) -> int:
    business = the_business(conn, args)
    category = books.find_category(conn, business["id"], args.category)
    books.map_category(conn, category, None if args.none else args.line, args.year)
    print(f"{category['code']} {category['name']}: " + ("off the form." if args.none else f"now on line {args.line}."))
    return 0


def cmd_chart_rename(args, conn) -> int:
    business = the_business(conn, args)
    category = books.find_category(conn, business["id"], args.category)
    books.rename_category(conn, category, args.new_name)
    print(f"{category['code']} is now {args.new_name}.")
    return 0


def cmd_chart_retire(args, conn) -> int:
    business = the_business(conn, args)
    category = books.find_category(conn, business["id"], args.category)
    books.retire_category(conn, category)
    print(f"{category['code']} {category['name']} is retired. Rows already booked to it stay and still report.")
    return 0


def _mapping_from_args(args, base: Optional[Mapping] = None) -> Optional[Mapping]:
    """Build a mapping from --layout, --mapping FILE and any column flags; flags win over the rest."""
    if args.layout and args.mapping:
        raise BooksError("give --layout or --mapping, not both")
    overrides = {
        "date_column": args.date_col,
        "date_format": args.date_format,
        "description_column": args.description_col,
        "amount_column": args.amount_col,
        "debit_column": args.debit_col,
        "credit_column": args.credit_col,
        "memo_column": args.memo_col,
        "reference_column": args.reference_col,
        "status_column": args.status_col,
        "skip_lines": args.skip_lines,
        "delimiter": args.delimiter,
        "encoding": args.encoding,
    }
    overrides = {name: value for name, value in overrides.items() if value is not None}
    if args.reference_col is not None and args.no_reference_col:
        raise BooksError("give --reference-col or --no-reference-col, not both")
    if args.invert_amount:
        overrides["invert_amount"] = True
    if args.no_invert_amount:
        overrides["invert_amount"] = False
    if args.no_reference_col:
        overrides["reference_column"] = None
    if not args.mapping and not args.layout and not overrides:
        return None
    if args.layout:
        mapping = layouts.get_layout(args.layout).mapping()
    elif args.mapping:
        mapping = Mapping.from_file(Path(args.mapping))
    else:
        mapping = base or Mapping()
    if args.debit_col and args.amount_col is None:
        mapping.amount_column = None
    if args.amount_col:
        mapping.debit_column = mapping.credit_column = None
    for name, value in overrides.items():
        setattr(mapping, name, value)
    mapping.validate()
    return mapping


def cmd_account_add(args, conn) -> int:
    business = the_business(conn, args)
    mapping = _mapping_from_args(args)
    if (args.opening_balance is None) != (args.opening_date is None):
        raise BooksError("give --opening-balance and --opening-date together")
    opening = None
    if args.opening_balance is not None:
        opening = parse_cents(args.opening_balance)
        if args.kind == "credit_card":
            opening = -opening
    account = books.add_account(
        conn,
        business,
        args.name,
        args.kind,
        args.institution or "",
        opening,
        parse_iso_date(args.opening_date) if args.opening_date else None,
        mapping.to_json() if mapping else None,
    )
    print(f"Added account {account['name']} ({account['kind']}) to {business['name']}.")
    if mapping is None:
        print(f"It has no column mapping yet. See 'mapping layouts', or try: {program_name()} mapping suggest EXPORT.csv")
    return 0


def _printed_balance_row(conn, account) -> str:
    summary = ledger.account_summary(conn, account)
    if summary["balance_cents"] is None:
        return "no opening balance"
    return f"{format_cents(books.book_to_printed(account, summary['balance_cents']))} on {summary['balance_on']}"


def cmd_account_list(args, conn) -> int:
    business_id = books.get_business(conn, args.business)["id"] if args.business else None
    rows = []
    for account in books.list_accounts(conn, business_id):
        count = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account["id"],)).fetchone()[0]
        rows.append(
            [
                account["name"],
                account["business_name"],
                account["kind"],
                str(count),
                _printed_balance_row(conn, account),
                "yes" if account["mapping_json"] else "NO",
            ]
        )
    print(render.table(["Account", "Business", "Kind", "Rows", "Balance", "Mapping"], rows, {3}))
    if any(row[2] == "credit_card" for row in rows):
        print("Card balances show what is owed.")
    return 0


def cmd_account_rename(args, conn) -> int:
    account = books.get_account(conn, args.name)
    print(f"{account['name']} is now {books.rename_account(conn, account, args.new_name)}.")
    return 0


def cmd_account_delete(args, conn) -> int:
    account = books.get_account(conn, args.name)
    count = books.delete_account(conn, account, with_rows=args.with_rows)
    print(f"Deleted {account['name']} and its {count} row(s).")
    return 0


def cmd_account_show(args, conn) -> int:
    account = books.get_account(conn, args.name)
    print(f"{account['name']}: {account['kind']}, {account['business_name']}")
    if account["institution"]:
        print(f"Institution: {account['institution']}")
    print(f"Balance: {_printed_balance_row(conn, account)}")
    if account["opening_date"]:
        print(
            f"Opening balance: {format_cents(books.book_to_printed(account, account['opening_balance_cents']))} "
            f"at the start of {account['opening_date']}"
        )
        early = ledger.rows_before_opening(conn, account)
        if early:
            print(f"WARNING: {early} row(s) are dated before the opening date and are left out of the balance.")
    print("Column mapping:")
    print(account["mapping_json"] or "  none yet")
    return 0


def cmd_account_map(args, conn) -> int:
    account = books.get_account(conn, args.name)
    base = Mapping.from_json(account["mapping_json"]) if account["mapping_json"] else None
    mapping = _mapping_from_args(args, base)
    if mapping is None:
        raise BooksError("nothing to set. Pass --layout ID, --mapping FILE or column flags such as --date-col.")
    books.set_account_mapping(conn, account, mapping.to_json())
    print(f"Mapping saved for {account['name']}. Check it with: import \"{account['name']}\" FILE --dry-run")
    return 0


def cmd_account_opening(args, conn) -> int:
    account = books.get_account(conn, args.name)
    books.set_opening_balance(
        conn, account, books.printed_to_book(account, parse_cents(args.balance)), parse_iso_date(args.date)
    )
    print(f"Opening balance set for {account['name']}.")
    return 0


def cmd_mapping_layouts(args) -> int:
    rows = [[layout.id, layout.name] for layout in layouts.load_layouts().values()]
    print(render.table(["Layout", "For"], rows, max_cell=90))
    print(f"\nUse one with: {program_name()} account map \"Your Account\" --layout ID")
    print("None of these has been checked against a live export. Always look at a dry run first. See docs/BANK-LAYOUTS.md.")
    return 0


def cmd_mapping_suggest(args) -> int:
    found = layouts.detect_layouts(Path(args.file))
    if found:
        mapping = found[0].mapping()
        notes = [f"This looks like layout {found[0].id}: {found[0].name}. Check the signs in a dry run."]
        notes.extend(f"It also fits layout {other.id}." for other in found[1:] if len(other.signature) == len(found[0].signature))
    else:
        mapping, notes = suggest_mapping(Path(args.file))
    text = mapping.to_json()
    if args.output:
        write_atomic(Path(args.output), (text + "\n").encode("utf-8"))
        print(f"Wrote {args.output}")
    else:
        print(text)
    for note in notes:
        print(f"NOTE: {note}", file=sys.stderr)
    return 0


def _add_mapping_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("column mapping")
    group.add_argument("--layout", help="a ready-made mapping for a bank's export (see 'mapping layouts')")
    group.add_argument("--mapping", help="JSON file with the whole mapping (see 'mapping suggest')")
    group.add_argument("--date-col")
    group.add_argument("--date-format", help="strptime format, for example %%m/%%d/%%Y")
    group.add_argument("--description-col")
    group.add_argument("--amount-col")
    group.add_argument("--debit-col")
    group.add_argument("--credit-col")
    group.add_argument("--memo-col")
    group.add_argument("--reference-col", help="column holding the bank's own unique row id")
    group.add_argument("--no-reference-col", action="store_true", help="stop treating any column as a unique row id")
    group.add_argument("--status-col", help="rows whose status is 'pending' are skipped")
    group.add_argument("--skip-lines", type=int, help="lines before the header row")
    group.add_argument("--delimiter")
    group.add_argument("--encoding")
    group.add_argument("--invert-amount", action="store_true", help="the export shows charges as positive")
    group.add_argument("--no-invert-amount", action="store_true")


def register(commands) -> None:
    business = add_group(commands, "business", "the businesses you keep books for")
    p = add_command(business, "add", cmd_business_add, "add a business with a starting chart of accounts")
    p.add_argument("name")
    p.add_argument("--activity", help="what the business does")
    add_command(business, "list", cmd_business_list, "list businesses")
    p = add_command(business, "rename", cmd_business_rename, "rename a business")
    p.add_argument("name")
    p.add_argument("new_name")
    p = add_command(business, "delete", cmd_business_delete, "delete a business that has no accounts")
    p.add_argument("name")

    chart = add_group(commands, "chart", "a business's chart of accounts")
    p = add_command(chart, "list", cmd_chart_list, "list categories and their form lines")
    p.add_argument("--business")
    p.add_argument("--year", type=year_arg, help="show line numbers for this tax year")
    p = add_command(chart, "add", cmd_chart_add, "add a category")
    p.add_argument("code")
    p.add_argument("name")
    p.add_argument("--kind", required=True, choices=books.CATEGORY_KINDS)
    p.add_argument("--line", help="form line number or key")
    p.add_argument("--year", type=year_arg, help="tax year the line number refers to (default: latest)")
    p.add_argument("--business")
    p = add_command(chart, "map", cmd_chart_map, "point a category at a Schedule C line")
    p.add_argument("category")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--line", help="form line number or key")
    target.add_argument("--none", action="store_true", help="take the category off the form")
    p.add_argument("--year", type=year_arg, help="tax year the line number refers to (default: latest)")
    p.add_argument("--business")
    p = add_command(chart, "rename", cmd_chart_rename, "rename a category")
    p.add_argument("category")
    p.add_argument("new_name")
    p.add_argument("--business")
    p = add_command(chart, "retire", cmd_chart_retire, "hide a category from pickers; booked rows stay")
    p.add_argument("category")
    p.add_argument("--business")

    account = add_group(commands, "account", "bank and card accounts")
    p = add_command(account, "add", cmd_account_add, "add an account")
    p.add_argument("name")
    p.add_argument("--kind", required=True, choices=books.ACCOUNT_KINDS)
    p.add_argument("--business")
    p.add_argument("--institution")
    p.add_argument("--opening-balance", help="balance at the start of the opening date; on a card, what was owed")
    p.add_argument("--opening-date", help="YYYY-MM-DD")
    _add_mapping_flags(p)
    p = add_command(account, "list", cmd_account_list, "list accounts and balances")
    p.add_argument("--business")
    p = add_command(account, "show", cmd_account_show, "show one account and its mapping")
    p.add_argument("name")
    p = add_command(account, "map", cmd_account_map, "set or change an account's column mapping")
    p.add_argument("name")
    _add_mapping_flags(p)
    p = add_command(account, "opening", cmd_account_opening, "set the opening balance")
    p.add_argument("name")
    p.add_argument("--balance", required=True, help="on a card, what was owed")
    p.add_argument("--date", required=True, help="YYYY-MM-DD; the balance at the start of this day")
    p = add_command(account, "rename", cmd_account_rename, "rename an account")
    p.add_argument("name")
    p.add_argument("new_name")
    p = add_command(account, "delete", cmd_account_delete, "delete an account")
    p.add_argument("name")
    p.add_argument("--with-rows", action="store_true", help="also delete its rows, which cannot be undone")

    mapping = add_group(commands, "mapping", "column mappings for exports")
    add_command(mapping, "layouts", cmd_mapping_layouts, "list the ready-made mappings for common banks and card issuers", needs_conn=False)
    p = add_command(mapping, "suggest", cmd_mapping_suggest, "recognize a bank's layout or guess a mapping from a sample export", needs_conn=False)
    p.add_argument("file")
    p.add_argument("-o", "--output", help="write the JSON to a file")
