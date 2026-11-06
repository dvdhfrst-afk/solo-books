"""The command line. Each command is a thin layer over the module that does the work."""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import webbrowser
from pathlib import Path
from typing import List, Optional

from . import __version__, books, db, importer, ledger, reconcile, render, reports, review, rules, schedule_c, web
from .csvmap import Mapping, suggest_mapping
from .errors import BooksError
from .money import format_cents, parse_cents
from .periods import parse_iso_date, parse_period, year_period
from .text import merchant_key, slug


# Setup

def cmd_init(args) -> int:
    path = db.resolve_db_path(args.db)
    if path.exists():
        print(f"Books already exist at {path}. Nothing changed.")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    db.connect(path, create=True).close()
    print(f"Created empty books at {path}.")
    print("Next: python -m solo_books business add \"Business A\"")
    return 0


def cmd_business_add(args, conn) -> int:
    business = books.add_business(conn, args.name, args.activity or "")
    count = len(books.list_categories(conn, business["id"]))
    print(f"Added {business['name']} with a starting chart of {count} categories. See 'chart list'.")
    return 0


def cmd_business_list(args, conn) -> int:
    rows = [[b["name"], b["activity"]] for b in books.list_businesses(conn)]
    print(render.table(["Business", "Activity"], rows))
    return 0


# Chart of accounts

def _current_line(form, category) -> str:
    key = category["schedule_c_key"]
    if not key:
        return ""
    entry = form.find_by_key(key)
    return entry["line"] if entry else f"({key}: not on {form.tax_year})"


def cmd_chart_list(args, conn) -> int:
    business = books.get_business(conn, args.business)
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
    business = books.get_business(conn, args.business)
    category = books.add_category(conn, business["id"], args.code, args.name, args.kind, args.line, args.year)
    print(f"Added {category['code']} {category['name']} to {business['name']}.")
    return 0


def cmd_chart_map(args, conn) -> int:
    business = books.get_business(conn, args.business)
    category = books.find_category(conn, business["id"], args.category)
    books.map_category(conn, category, None if args.none else args.line, args.year)
    print(f"{category['code']} {category['name']}: " + ("off the form." if args.none else f"now on line {args.line}."))
    return 0


def cmd_chart_rename(args, conn) -> int:
    business = books.get_business(conn, args.business)
    category = books.find_category(conn, business["id"], args.category)
    books.rename_category(conn, category, args.new_name)
    print(f"{category['code']} is now {args.new_name}.")
    return 0


def cmd_chart_retire(args, conn) -> int:
    business = books.get_business(conn, args.business)
    category = books.find_category(conn, business["id"], args.category)
    books.retire_category(conn, category)
    print(f"{category['code']} {category['name']} is retired. Rows already booked to it stay and still report.")
    return 0


# Accounts and mappings

def _mapping_from_args(args, base: Optional[Mapping] = None) -> Optional[Mapping]:
    """Build a mapping from --mapping FILE and any column flags; flags win over the file."""
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
    if not args.mapping and not overrides:
        return None
    mapping = Mapping.from_file(Path(args.mapping)) if args.mapping else (base or Mapping())
    if args.debit_col and args.amount_col is None:
        mapping.amount_column = None
    if args.amount_col:
        mapping.debit_column = mapping.credit_column = None
    for name, value in overrides.items():
        setattr(mapping, name, value)
    mapping.validate()
    return mapping


def cmd_account_add(args, conn) -> int:
    business = books.get_business(conn, args.business)
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
        print("It has no column mapping yet. Try: python -m solo_books mapping suggest EXPORT.csv")
    return 0


def _printed_balance_row(conn, account) -> str:
    latest = conn.execute(
        "SELECT MAX(posted_on) AS day FROM transactions WHERE account_id = ?", (account["id"],)
    ).fetchone()["day"]
    if account["opening_balance_cents"] is None:
        return "no opening balance"
    day = max(latest or account["opening_date"], account["opening_date"])
    balance = ledger.balance_through(conn, account, parse_iso_date(day))
    return f"{format_cents(books.book_to_printed(account, balance))} on {day}"


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
    print("Card balances show what is owed.")
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
        raise BooksError("nothing to set. Pass --mapping FILE or column flags such as --date-col.")
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


def cmd_mapping_suggest(args) -> int:
    mapping, notes = suggest_mapping(Path(args.file))
    text = mapping.to_json()
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(text)
    for note in notes:
        print(f"NOTE: {note}", file=sys.stderr)
    return 0


# Importing

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


def cmd_import(args, conn) -> int:
    account = books.get_account(conn, args.account)
    result = importer.import_export(conn, account, Path(args.file), dry_run=args.dry_run, skip_bad=args.skip_bad)
    _print_import(result)
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
    print("Any of them that a later file also held are gone too. Import that file again to bring them back.")
    return 0


# Rules

def _rule_match(args) -> tuple:
    chosen = [(kind, value) for kind, value in (
        ("contains", args.contains), ("starts", args.starts), ("exact", args.exact), ("regex", args.regex),
    ) if value]
    if len(chosen) != 1:
        raise BooksError("give exactly one of --contains, --starts, --exact, --regex")
    return chosen[0]


def cmd_rules_add(args, conn) -> int:
    business = books.get_business(conn, args.business)
    match_type, pattern = _rule_match(args)
    category = books.find_category(conn, business["id"], args.category)
    account_id = None
    if args.account:
        account = books.get_account(conn, args.account)
        if account["business_id"] != business["id"]:
            raise BooksError(f"account {account['name']} belongs to another business")
        account_id = account["id"]
    rule_id = rules.add_rule(
        conn,
        business["id"],
        match_type,
        pattern,
        category,
        priority=args.priority,
        account_id=account_id,
        direction=args.direction,
        min_cents=parse_cents(args.min) if args.min else None,
        max_cents=parse_cents(args.max) if args.max else None,
    )
    outcome = rules.apply_rules(conn, business["id"])
    print(f"Added rule {rule_id}: {match_type} {pattern!r} -> {category['code']} {category['name']}.")
    print(f"It and the other rules booked {outcome['categorized']} row(s) waiting in the queue.")
    return 0


def cmd_rules_list(args, conn) -> int:
    business = books.get_business(conn, args.business)
    rows = []
    for rule in rules.list_rules(conn, business["id"]):
        limits = []
        if rule["direction"]:
            limits.append(rule["direction"])
        if rule["min_cents"] is not None:
            limits.append(f">={format_cents(rule['min_cents'])}")
        if rule["max_cents"] is not None:
            limits.append(f"<={format_cents(rule['max_cents'])}")
        if rule["account_name"]:
            limits.append(rule["account_name"])
        rows.append(
            [
                str(rule["id"]),
                str(rule["priority"]),
                rule["match_type"],
                rule["pattern"],
                f"{rule['category_code']} {rule['category_name']}",
                " ".join(limits),
                str(rule["hits"]),
            ]
        )
    print(render.table(["Id", "Prio", "Match", "Pattern", "Category", "Only when", "Rows"], rows, {0, 1, 6}))
    return 0


def cmd_rules_delete(args, conn) -> int:
    rules.delete_rule(conn, args.id)
    print(f"Deleted rule {args.id}. Rows it booked keep their category.")
    return 0


def cmd_rules_apply(args, conn) -> int:
    businesses = [books.get_business(conn, args.business)] if args.business else books.list_businesses(conn)
    for business in businesses:
        outcome = rules.apply_rules(conn, business["id"], recheck=args.recheck)
        print(
            f"{business['name']}: booked {outcome['categorized']}, changed {outcome['changed']}, "
            f"back to review {outcome['returned_to_review']}."
        )
    return 0


def cmd_rules_test(args, conn) -> int:
    business = books.get_business(conn, args.business)
    amount = parse_cents(args.amount) if args.amount else -100
    account_id = books.get_account(conn, args.account)["id"] if args.account else -1
    rule = rules.first_match(
        rules.load_rules(conn, business["id"]), account_id, args.description, "", merchant_key(args.description), amount
    )
    print(f"Merchant reads as: {merchant_key(args.description)}")
    if rule is None:
        print("No rule matches. This row would wait in the review queue.")
        if not args.amount:
            print("Tested as 1.00 of money out. Rules limited by direction or size need --amount.")
        return 1
    category = conn.execute("SELECT * FROM categories WHERE id = ?", (rule.category_id,)).fetchone()
    print(f"Rule {rule.id} ({rule.match_type} {rule.pattern!r}) books it to {category['code']} {category['name']}.")
    return 0


def cmd_rules_export(args, conn) -> int:
    business = books.get_business(conn, args.business)
    render.deliver(rules.export_rules(conn, business["id"]), args.output)
    return 0


def cmd_rules_import(args, conn) -> int:
    business = books.get_business(conn, args.business)
    count = rules.import_rules(conn, business["id"], Path(args.file))
    outcome = rules.apply_rules(conn, business["id"])
    print(f"Added {count} rule(s) to {business['name']}; they booked {outcome['categorized']} queued row(s).")
    return 0


# Review queue and rows

def _scope_ids(conn, args) -> tuple:
    business_id = books.get_business(conn, args.business)["id"] if args.business else None
    account_id = books.get_account(conn, args.account)["id"] if args.account else None
    return business_id, account_id


def cmd_review(args, conn) -> int:
    business_id, account_id = _scope_ids(conn, args)
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
            [str(r["id"]), r["posted_on"], r["account_name"], format_cents(r["amount_cents"]), r["description"]]
            for r in rows
        ]
        print(render.table(["Id", "Date", "Account", "Amount", "Description"], table_rows, {0, 3}))
        print(f"{len(rows)} row(s) waiting.")
        return 0
    if not rows:
        print("Nothing waiting for review.")
        return 0
    print(f"{len(rows)} row(s) waiting. Type ? for help.")
    totals = review.run_interactive(conn, business_id, account_id, input, print)
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


TXN_HEADERS = ["Id", "Date", "Account", "Amount", "Description", "Category", "By", "Rec"]


def cmd_txn_list(args, conn) -> int:
    business_id, account_id = _scope_ids(conn, args)
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
        render.deliver(render.csv_text(render.DETAIL_CSV_HEADERS, render.detail_csv_rows(rows, form)), args.output)
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


def cmd_txn_delete(args, conn) -> int:
    for transaction_id in args.ids:
        ledger.delete_transaction(conn, transaction_id)
        print(f"Deleted transaction {transaction_id}.")
    return 0


def cmd_txn_note(args, conn) -> int:
    ledger.set_note(conn, args.id, args.note)
    print(f"Note saved on transaction {args.id}.")
    return 0


# Reconciliation

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
    print(render.reconcile_text(report))
    return 0 if report.balanced else 1


def cmd_reconcile_show(args, conn) -> int:
    report = reconcile.compute(
        conn, reconcile.get(conn, args.id), Path(args.statement) if args.statement else None, args.skip_bad
    )
    print(render.reconcile_text(report))
    return 0 if report.balanced else 1


def cmd_reconcile_finish(args, conn) -> int:
    report = reconcile.finish(conn, args.id)
    print(
        f"Reconciliation {args.id} finished: {report.account['name']}, {report.start} to {report.end}, "
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
                recon["period_start"],
                recon["period_end"],
                reconcile.printed(report.account, recon["statement_begin_cents"]),
                reconcile.printed(report.account, recon["statement_end_cents"]),
                format_cents(report.difference),
                recon["status"],
            ]
        )
    print(render.table(["Id", "Account", "From", "To", "Begin", "End", "Diff", "Status"], rows, {0, 4, 5, 6}))
    return 0


# Reports

def _businesses(conn, name: Optional[str]) -> List[sqlite3.Row]:
    if name:
        return [books.get_business(conn, name)]
    rows = books.list_businesses(conn)
    if not rows:
        raise BooksError("no businesses yet. Add one with 'business add NAME'.")
    return rows


def cmd_report_pnl(args, conn) -> int:
    period = parse_period(args.period)
    reports_list = [reports.profit_and_loss(conn, b, period, args.by) for b in _businesses(conn, args.business)]
    if args.format == "csv":
        rows: List[List[str]] = []
        for pnl in reports_list:
            rows.extend(render.pnl_csv_rows(pnl))
        render.deliver(render.csv_text(render.PNL_CSV_HEADERS, rows), args.output)
        if args.output:
            print(f"Wrote {args.output}")
        return 0
    chunks = [render.pnl_text(pnl) for pnl in reports_list]
    if len(reports_list) > 1:
        rows = [
            [
                pnl.business,
                format_cents(sum(pnl.total_income)),
                format_cents(sum(pnl.total_cogs)),
                format_cents(sum(pnl.total_expenses)),
                format_cents(sum(pnl.net)),
            ]
            for pnl in reports_list
        ]
        rows.append(
            [
                "All businesses",
                format_cents(sum(sum(p.total_income) for p in reports_list)),
                format_cents(sum(sum(p.total_cogs) for p in reports_list)),
                format_cents(sum(sum(p.total_expenses) for p in reports_list)),
                format_cents(sum(sum(p.net) for p in reports_list)),
            ]
        )
        chunks.append(
            f"All businesses, {period.label}\n"
            + render.table(["Business", "Income", "Cost of goods", "Expenses", "Net"], rows, {1, 2, 3, 4})
        )
    render.deliver("\n\n".join(chunks), args.output)
    return 0


def cmd_report_schedule_c(args, conn) -> int:
    businesses = _businesses(conn, args.business)
    results = [reports.schedule_c(conn, b, args.year, args.lines_year) for b in businesses]
    if args.format == "csv":
        rows: List[List[str]] = []
        for result in results:
            rows.extend(render.schedule_c_csv_rows(result))
        render.deliver(render.csv_text(render.SCHEDULE_CSV_HEADERS, rows), args.output)
        if args.output:
            print(f"Wrote {args.output}")
        return 0
    render.deliver("\n\n".join(render.schedule_c_text(r, args.all_lines) for r in results), args.output)
    return 0


def cmd_report_preparer(args, conn) -> int:
    """One folder a tax preparer can work from: the rollup, the quarters, and every row behind them."""
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    period = year_period(args.year)
    for business in _businesses(conn, args.business):
        name = slug(business["name"])
        result = reports.schedule_c(conn, business, args.year, args.lines_year)
        rollup = out_dir / f"{name}-schedule-c-{args.year}.csv"
        render.deliver(render.csv_text(render.SCHEDULE_CSV_HEADERS, render.schedule_c_csv_rows(result)), str(rollup))
        pnl = reports.profit_and_loss(conn, business, period, "quarter")
        pnl_path = out_dir / f"{name}-profit-and-loss-{args.year}.csv"
        render.deliver(render.csv_text(render.PNL_CSV_HEADERS, render.pnl_csv_rows(pnl)), str(pnl_path))
        rows = ledger.find_transactions(conn, business_id=business["id"], period=period)
        detail = out_dir / f"{name}-transactions-{args.year}.csv"
        render.deliver(render.csv_text(render.DETAIL_CSV_HEADERS, render.detail_csv_rows(rows, result.form)), str(detail))
        print(f"{business['name']}: line 31 {format_cents(result.net_line.cents)}, {len(rows)} rows")
        print(f"  {rollup}\n  {pnl_path}\n  {detail}")
        if result.uncategorized_count:
            print(f"  WARNING: {result.uncategorized_count} uncategorized row(s) are not in the figures. Run 'review' first.")
        for warning in result.warnings:
            print(f"  WARNING: {warning}")
    print(reports.NOT_ADVICE)
    return 0


def cmd_inventory_set(args, conn) -> int:
    business = books.get_business(conn, args.business)
    books.set_inventory(conn, business["id"], args.year, parse_cents(args.beginning), parse_cents(args.ending))
    print(f"Inventory counts saved for {business['name']}, {args.year}.")
    return 0


def cmd_status(args, conn) -> int:
    rows = []
    for business in books.list_businesses(conn):
        waiting = len(review.queue(conn, business["id"]))
        for account in books.list_accounts(conn, business["id"]):
            stats = conn.execute(
                "SELECT COUNT(*) AS rows_count, MAX(posted_on) AS latest FROM transactions WHERE account_id = ?",
                (account["id"],),
            ).fetchone()
            through = conn.execute(
                "SELECT MAX(period_end) AS day FROM reconciliations WHERE account_id = ? AND status = 'finished'",
                (account["id"],),
            ).fetchone()["day"]
            rows.append(
                [business["name"], account["name"], str(stats["rows_count"]), stats["latest"] or "-", through or "never"]
            )
        print(f"{business['name']}: {waiting} row(s) waiting for review")
    print()
    print(render.table(["Business", "Account", "Rows", "Latest row", "Reconciled through"], rows, {2}))
    return 0


def cmd_backup(args, conn) -> int:
    db.backup(conn, Path(args.file))
    print(f"Backed up to {args.file}.")
    return 0


def cmd_web(args) -> int:
    path = db.resolve_db_path(args.db)
    db.connect(path).close()
    try:
        server = web.ReviewServer(path, args.port, verbose=args.verbose)
    except OSError as exc:
        raise BooksError(f"cannot listen on port {args.port}: {exc.strerror}. Try --port.") from None
    print(f"Review queue at {server.url}  (Ctrl+C stops it)")
    if args.open:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


# Argument parsing

def _add_mapping_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("column mapping")
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


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help=f"books file (default ${db.DB_ENV_VAR}, else ./{db.DEFAULT_DB_NAME})")

    parser = argparse.ArgumentParser(
        prog="python -m solo_books",
        description="Local bookkeeping for one person who owns one or more single-member LLCs.",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"solo-books {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def add(group, name, handler, help_text, needs_conn=True):
        sub = group.add_parser(name, help=help_text, description=help_text, parents=[common])
        sub.set_defaults(handler=handler, needs_conn=needs_conn)
        return sub

    def nested(name, help_text):
        sub = commands.add_parser(name, help=help_text, description=help_text, parents=[common])
        return sub.add_subparsers(dest=f"{name}_command", metavar="ACTION", required=True)

    add(commands, "init", cmd_init, "create empty books", needs_conn=False)
    add(commands, "status", cmd_status, "what is waiting, and how far each account is reconciled")
    p = add(commands, "backup", cmd_backup, "write a consistent copy of the books to a new file")
    p.add_argument("file")

    business = nested("business", "the businesses you keep books for")
    p = add(business, "add", cmd_business_add, "add a business with a starting chart of accounts")
    p.add_argument("name")
    p.add_argument("--activity", help="what the business does")
    add(business, "list", cmd_business_list, "list businesses")

    chart = nested("chart", "a business's chart of accounts")
    p = add(chart, "list", cmd_chart_list, "list categories and their form lines")
    p.add_argument("--business")
    p.add_argument("--year", type=int, help="show line numbers for this tax year")
    p = add(chart, "add", cmd_chart_add, "add a category")
    p.add_argument("code")
    p.add_argument("name")
    p.add_argument("--kind", required=True, choices=books.CATEGORY_KINDS)
    p.add_argument("--line", help="form line number or key")
    p.add_argument("--year", type=int, help="tax year the line number refers to (default: latest)")
    p.add_argument("--business")
    p = add(chart, "map", cmd_chart_map, "point a category at a Schedule C line")
    p.add_argument("category")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--line", help="form line number or key")
    target.add_argument("--none", action="store_true", help="take the category off the form")
    p.add_argument("--year", type=int, help="tax year the line number refers to (default: latest)")
    p.add_argument("--business")
    p = add(chart, "rename", cmd_chart_rename, "rename a category")
    p.add_argument("category")
    p.add_argument("new_name")
    p.add_argument("--business")
    p = add(chart, "retire", cmd_chart_retire, "hide a category from pickers; booked rows stay")
    p.add_argument("category")
    p.add_argument("--business")

    account = nested("account", "bank and card accounts")
    p = add(account, "add", cmd_account_add, "add an account")
    p.add_argument("name")
    p.add_argument("--kind", required=True, choices=books.ACCOUNT_KINDS)
    p.add_argument("--business")
    p.add_argument("--institution")
    p.add_argument("--opening-balance", help="balance at the start of the opening date; on a card, what was owed")
    p.add_argument("--opening-date", help="YYYY-MM-DD")
    _add_mapping_flags(p)
    p = add(account, "list", cmd_account_list, "list accounts and balances")
    p.add_argument("--business")
    p = add(account, "show", cmd_account_show, "show one account and its mapping")
    p.add_argument("name")
    p = add(account, "map", cmd_account_map, "set or change an account's column mapping")
    p.add_argument("name")
    _add_mapping_flags(p)
    p = add(account, "opening", cmd_account_opening, "set the opening balance")
    p.add_argument("name")
    p.add_argument("--balance", required=True, help="on a card, what was owed")
    p.add_argument("--date", required=True, help="YYYY-MM-DD; the balance at the start of this day")

    mapping = nested("mapping", "column mappings for exports")
    p = add(mapping, "suggest", cmd_mapping_suggest, "guess a mapping from a sample export", needs_conn=False)
    p.add_argument("file")
    p.add_argument("-o", "--output", help="write the JSON to a file")

    p = add(commands, "import", cmd_import, "import a bank or card CSV; safe to repeat with overlapping files")
    p.add_argument("account")
    p.add_argument("file")
    p.add_argument("--dry-run", action="store_true", help="report what would happen and save nothing")
    p.add_argument("--skip-bad", action="store_true", help="import the readable rows even if some are not")

    imports = nested("imports", "past imports")
    p = add(imports, "list", cmd_imports_list, "list imports")
    p.add_argument("--account")
    p = add(imports, "undo", cmd_imports_undo, "take a whole import back out")
    p.add_argument("id", type=int)
    p.add_argument("--force", action="store_true", help="undo even if rows were categorized by hand")

    rule = nested("rules", "categorization rules")
    p = add(rule, "add", cmd_rules_add, "add a rule and run it over the queue")
    p.add_argument("--category", required=True)
    p.add_argument("--contains")
    p.add_argument("--starts")
    p.add_argument("--exact")
    p.add_argument("--regex")
    p.add_argument("--priority", type=int, default=100, help="lower numbers are tried first (default 100)")
    p.add_argument("--direction", choices=("in", "out"))
    p.add_argument("--min", help="only rows of at least this size")
    p.add_argument("--max", help="only rows of at most this size")
    p.add_argument("--account", help="only rows in this account")
    p.add_argument("--business")
    p = add(rule, "list", cmd_rules_list, "list rules in the order they are tried")
    p.add_argument("--business")
    p = add(rule, "delete", cmd_rules_delete, "delete a rule; booked rows stay booked")
    p.add_argument("id", type=int)
    p = add(rule, "apply", cmd_rules_apply, "run the rules over the review queue")
    p.add_argument("--business")
    p.add_argument("--recheck", action="store_true", help="also re-run over rows rules booked earlier")
    p = add(rule, "test", cmd_rules_test, "see which rule a description would hit")
    p.add_argument("description")
    p.add_argument("--amount", help="signed amount, default -1.00")
    p.add_argument("--account")
    p.add_argument("--business")
    p = add(rule, "export", cmd_rules_export, "write rules as CSV")
    p.add_argument("--business")
    p.add_argument("-o", "--output")
    p = add(rule, "import", cmd_rules_import, "add rules from a CSV")
    p.add_argument("file")
    p.add_argument("--business")

    p = add(commands, "review", cmd_review, "work through rows no rule could book")
    p.add_argument("--business")
    p.add_argument("--account")
    p.add_argument("--list", action="store_true", help="print the queue and exit")
    p.add_argument("--groups", action="store_true", help="print the queue by merchant and exit")

    p = add(commands, "categorize", cmd_categorize, "book rows to a category by hand")
    p.add_argument("category", nargs="?", help="category code or name")
    p.add_argument("ids", nargs="*", type=int, help="transaction ids")
    p.add_argument("--clear", action="store_true", help="send the rows back to the queue instead")

    txn = nested("txn", "individual rows")
    p = add(txn, "list", cmd_txn_list, "find rows")
    p.add_argument("--business")
    p.add_argument("--account")
    p.add_argument("--period", help="2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20")
    p.add_argument("--category")
    p.add_argument("--uncategorized", action="store_true")
    p.add_argument("--search")
    p.add_argument("--limit", type=int)
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add(txn, "add", cmd_txn_add, "add a row by hand")
    p.add_argument("account")
    p.add_argument("--date", required=True)
    p.add_argument("--amount", required=True, help="signed: negative is money out")
    p.add_argument("--description", required=True)
    p.add_argument("--category")
    p.add_argument("--note")
    p = add(txn, "delete", cmd_txn_delete, "delete rows; later imports will not bring them back")
    p.add_argument("ids", nargs="+", type=int)
    p = add(txn, "note", cmd_txn_note, "attach a note to a row")
    p.add_argument("id", type=int)
    p.add_argument("note")

    rec = nested("reconcile", "tie an account to a statement")
    p = add(rec, "run", cmd_reconcile_run, "compare the books to a statement period; exit status 1 if they differ")
    p.add_argument("account")
    p.add_argument("--from", dest="start", required=True, help="first day of the statement, YYYY-MM-DD")
    p.add_argument("--to", dest="end", required=True, help="last day of the statement, YYYY-MM-DD")
    p.add_argument("--begin", required=True, help="beginning balance as printed; on a card, what was owed")
    p.add_argument("--end", dest="finish", required=True, help="ending balance as printed; on a card, what was owed")
    p.add_argument("--statement", help="CSV of the statement's lines, compared line by line")
    p.add_argument("--skip-bad", action="store_true", help="compare the readable statement rows even if some are not")
    p = add(rec, "show", cmd_reconcile_show, "show a reconciliation again")
    p.add_argument("id", type=int)
    p.add_argument("--statement", help="CSV of the statement's lines, compared line by line")
    p.add_argument("--skip-bad", action="store_true", help="compare the readable statement rows even if some are not")
    p = add(rec, "finish", cmd_reconcile_finish, "lock a period that ties to the cent")
    p.add_argument("id", type=int)
    p = add(rec, "reopen", cmd_reconcile_reopen, "unlock the latest finished period")
    p.add_argument("id", type=int)
    p = add(rec, "forget", cmd_reconcile_forget, "remove an open reconciliation")
    p.add_argument("id", type=int)
    p = add(rec, "list", cmd_reconcile_list, "list reconciliations")
    p.add_argument("--account")

    report = nested("report", "profit and loss, Schedule C rollup, preparer export")
    p = add(report, "pnl", cmd_report_pnl, "profit and loss by period and business")
    p.add_argument("--period", required=True, help="2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20")
    p.add_argument("--by", choices=("total", "month", "quarter", "year"), default="total")
    p.add_argument("--business", help="default: every business")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add(report, "schedule-c", cmd_report_schedule_c, "roll one tax year up to Schedule C lines")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--lines-year", type=int, help="use another year's form lines, for a year with no data file")
    p.add_argument("--business", help="default: every business")
    p.add_argument("--all-lines", action="store_true", help="show lines with nothing on them too")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add(report, "preparer", cmd_report_preparer, "write a folder of CSV files for a tax preparer")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--out", required=True, help="folder to write into")
    p.add_argument("--lines-year", type=int)
    p.add_argument("--business", help="default: every business")

    inventory = nested("inventory", "beginning and ending inventory for cost of goods sold")
    p = add(inventory, "set", cmd_inventory_set, "save a year's two counts")
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--beginning", required=True)
    p.add_argument("--ending", required=True)
    p.add_argument("--business")

    p = add(commands, "web", cmd_web, "open the review queue as a local web page", needs_conn=False)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open it in your browser")
    p.add_argument("--verbose", action="store_true")
    return parser


_NEGATIVE_AMOUNT = re.compile(r"^-\$?[\d,]*\.?\d*$")


def _attach_negative_amounts(argv: List[str]) -> List[str]:
    """Let --amount -1,234.50 work. argparse alone takes that value for an unknown flag."""
    result: List[str] = []
    for arg in argv:
        if result and result[-1].startswith("--") and "=" not in result[-1] and _NEGATIVE_AMOUNT.match(arg) and arg != "-":
            result[-1] = f"{result[-1]}={arg}"
        else:
            result.append(arg)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    args = parser.parse_args(_attach_negative_amounts(sys.argv[1:] if argv is None else list(argv)))
    if not hasattr(args, "db"):
        args.db = None
    try:
        if not args.needs_conn:
            return args.handler(args)
        conn = db.connect(db.resolve_db_path(args.db))
        try:
            return args.handler(args, conn)
        finally:
            conn.close()
    except BooksError as exc:
        print(f"solo-books: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0
    except sqlite3.OperationalError as exc:
        print(f"solo-books: the books file could not be used: {exc}", file=sys.stderr)
        return 2
