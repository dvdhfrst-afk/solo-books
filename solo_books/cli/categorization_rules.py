"""Categorization rules: adding, listing, testing, and moving them between businesses."""
from __future__ import annotations

from pathlib import Path

from .. import books, render, rules
from ..errors import BooksError
from ..money import format_cents, parse_cents
from ..text import merchant_key
from .common import add_command, add_group, deliver


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
    deliver(rules.export_rules(conn, business["id"]), args.output)
    return 0


def cmd_rules_import(args, conn) -> int:
    business = books.get_business(conn, args.business)
    count = rules.import_rules(conn, business["id"], Path(args.file))
    outcome = rules.apply_rules(conn, business["id"])
    print(f"Added {count} rule(s) to {business['name']}; they booked {outcome['categorized']} queued row(s).")
    return 0


def register(commands) -> None:
    rule = add_group(commands, "rules", "categorization rules")
    p = add_command(rule, "add", cmd_rules_add, "add a rule and run it over the queue")
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
    p = add_command(rule, "list", cmd_rules_list, "list rules in the order they are tried")
    p.add_argument("--business")
    p = add_command(rule, "delete", cmd_rules_delete, "delete a rule; booked rows stay booked")
    p.add_argument("id", type=int)
    p = add_command(rule, "apply", cmd_rules_apply, "run the rules over the review queue")
    p.add_argument("--business")
    p.add_argument("--recheck", action="store_true", help="also re-run over rows rules booked earlier")
    p = add_command(rule, "test", cmd_rules_test, "see which rule a description would hit")
    p.add_argument("description")
    p.add_argument("--amount", help="signed amount, default -1.00")
    p.add_argument("--account")
    p.add_argument("--business")
    p = add_command(rule, "export", cmd_rules_export, "write rules as CSV")
    p.add_argument("--business")
    p.add_argument("-o", "--output")
    p = add_command(rule, "import", cmd_rules_import, "add rules from a CSV")
    p.add_argument("file")
    p.add_argument("--business")
