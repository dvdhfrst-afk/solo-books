"""Categorization rules: adding, changing, ordering, explaining, testing, and moving them between businesses."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import List, Optional

from .. import books, ledger, render, rule_packs, rules
from ..conditions import (
    GROUPS,
    Condition,
    Facts,
    account_condition,
    amount_condition,
    describe_all,
    direction_condition,
    first_and_last_day,
    day_range,
    text_condition,
)
from ..errors import BooksError
from ..files import write_atomic
from ..money import format_cents, parse_cents
from ..periods import parse_iso_date
from ..text import merchant_key
from .common import EXIT_FINDING, add_command, add_group, deliver, program_name, the_business

_WORDING_FLAGS = (("contains", "contains"), ("starts", "starts"), ("exact", "exact"), ("regex", "regex"))


def _day_arg(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a day of the month: {text!r}") from None
    if not 1 <= value <= 31:
        raise argparse.ArgumentTypeError(f"a day of the month is from 1 to 31: {text!r}")
    return value


def _conditions(conn, business, args) -> List[Condition]:
    """The conditions the flags ask for, one per group at most."""
    found: List[Condition] = []
    wording = [(kind, getattr(args, attr)) for attr, kind in _WORDING_FLAGS if getattr(args, attr)]
    if len(wording) > 1:
        raise BooksError("give at most one of --contains, --starts-with, --exact, --regex")
    if wording:
        found.append(text_condition(*wording[0]))
    if args.min or args.max:
        found.append(amount_condition(parse_cents(args.min) if args.min else None, parse_cents(args.max) if args.max else None))
    if args.direction:
        found.append(direction_condition(args.direction))
    if args.account:
        account = books.get_account(conn, args.account)
        if account["business_id"] != business["id"]:
            raise BooksError(f"account {account['name']} belongs to another business")
        found.append(account_condition(account["id"]))
    if args.date_from or args.date_to:
        found.append(first_and_last_day(args.date_from, args.date_to))
    if args.day_from is not None or args.day_to is not None:
        found.append(day_range(args.day_from, args.day_to))
    return found


def _category_text(conn, category_id: int) -> str:
    category = conn.execute("SELECT code, name FROM categories WHERE id = ?", (category_id,)).fetchone()
    return f"{category['code']} {category['name']}"


def _say_rule(conn, rule: rules.Rule) -> str:
    said = f"{describe_all(rule.conditions, rules.account_names(conn))} -> {_category_text(conn, rule.category_id)}"
    return said + (f", note {rule.note!r}" if rule.note else "")


def cmd_rules_add(args, conn) -> int:
    business = the_business(conn, args)
    conditions = _conditions(conn, business, args)
    if not conditions:
        raise BooksError(
            "a rule needs at least one condition. Give exactly one of --contains, --starts-with, --exact or --regex, "
            "or another condition such as --amount-min. 'rules add --help' lists them."
        )
    category = books.find_category(conn, business["id"], args.category)
    rule_id = rules.create_rule(conn, business["id"], conditions, category, priority=args.priority, note=args.note or "")
    outcome = rules.apply_rules(conn, business["id"])
    print(f"Added rule {rule_id}: {_say_rule(conn, rules.get_rule(conn, rule_id))}.")
    print(f"It and the other rules booked {outcome['categorized']} row(s) waiting in the queue.")
    return 0


def cmd_rules_edit(args, conn) -> int:
    rule = rules.get_rule(conn, args.id)
    business = conn.execute("SELECT * FROM businesses WHERE id = ?", (rule.business_id,)).fetchone()
    conditions = _conditions(conn, business, args)
    category = books.find_category(conn, business["id"], args.category) if args.category else None
    note = "" if args.no_note else args.note
    if not conditions and not args.drop and category is None and note is None:
        raise BooksError("say what to change: a condition, --drop, --category, --note or --no-note")
    changed = rules.edit_rule(conn, args.id, conditions, args.drop or (), category, note)
    outcome = rules.apply_rules(conn, business["id"])
    print(f"Rule {args.id} is now: {_say_rule(conn, changed)}.")
    print(f"The rules booked {outcome['categorized']} row(s) waiting in the queue.")
    print(f"Rows it booked earlier stay as they are until '{program_name()} rules apply --recheck'.")
    return 0


def cmd_rules_list(args, conn) -> int:
    business = the_business(conn, args)
    names = rules.account_names(conn)
    hits = {row["id"]: row for row in rules.list_rules(conn, business["id"])}
    rows = []
    for place, rule in enumerate(rules.load_rules(conn, business["id"], include_disabled=True), start=1):
        listed = hits[rule.id]
        rows.append(
            [
                str(place),
                str(rule.id),
                str(rule.priority),
                "yes" if rule.enabled else "off",
                describe_all(rule.conditions, names),
                f"{listed['category_code']} {listed['category_name']}" + (f"; note {rule.note!r}" if rule.note else ""),
                str(listed["hits"]),
            ]
        )
    print(render.table(["#", "Id", "Prio", "On", "Conditions", "Books to", "Rows"], rows, {0, 1, 2, 6}, max_cell=72))
    return 0


def cmd_rules_move(args, conn) -> int:
    place = rules.move_rule(conn, args.id, before=args.before, position=args.to)
    print(f"Rule {args.id} is now number {place} in the order. Rules are numbered 1, 2, 3 by priority.")
    print(f"Rows already booked stay as they are until '{program_name()} rules apply --recheck'.")
    return 0


def cmd_rules_disable(args, conn) -> int:
    rules.set_enabled(conn, args.id, False)
    print(f"Rule {args.id} is disabled. It keeps its place and books nothing until it is enabled.")
    print(f"Rows it booked earlier stay booked; '{program_name()} rules apply --recheck' sends them through the other rules.")
    return 0


def cmd_rules_enable(args, conn) -> int:
    rules.set_enabled(conn, args.id, True)
    rule = rules.get_rule(conn, args.id)
    outcome = rules.apply_rules(conn, rule.business_id or 0)
    print(f"Rule {args.id} is enabled. The rules booked {outcome['categorized']} row(s) waiting in the queue.")
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


def _print_trials(conn, explanation: rules.Explanation) -> None:
    if not explanation.trials:
        print("This business has no rules.")
        return
    print("Rules in the order they are tried:")
    for trial in explanation.trials:
        print(f"  {trial.place}. rule {trial.rule.id} -> {_category_text(conn, trial.rule.category_id)}: {trial.verdict}")
        for line in trial.lines:
            print(f"       {line}")


def cmd_rules_explain(args, conn) -> int:
    row = ledger.get_transaction(conn, args.id)
    explanation = rules.explain(conn, args.id)
    print(
        f"Transaction {row['id']}: {args.settings.show_date(row['posted_on'])}  {format_cents(row['amount_cents'])}  "
        f"{row['description']} ({row['account_name']}, {row['business_name']})"
    )
    print(f"Merchant reads as: {row['merchant']}")
    if row["memo"]:
        print(f"Memo: {row['memo']}")
    _print_trials(conn, explanation)
    winner = explanation.winner
    if row["category_source"] == "manual":
        print("It was booked by hand, so rules leave it alone.")
    elif row["category_source"] == "rule" and winner is not None and row["rule_id"] != winner.id:
        print(f"It is booked by rule {row['rule_id']}, but rule {winner.id} matches first now. "
              f"'{program_name()} rules apply --recheck' rebooks it.")
    elif row["category_source"] == "rule" and winner is None:
        print(f"It is booked by rule {row['rule_id']}, which no longer matches. "
              f"'{program_name()} rules apply --recheck' sends it back to review.")
    elif row["category_id"] is None and winner is not None:
        print(f"It is waiting for review. '{program_name()} rules apply' books it with rule {winner.id}.")
    if winner is None:
        print("No enabled rule books this row.")
        return EXIT_FINDING
    return 0


def cmd_rules_test(args, conn) -> int:
    business = the_business(conn, args)
    amount = parse_cents(args.amount) if args.amount else -100
    account_id = books.get_account(conn, args.account)["id"] if args.account else -1
    day: Optional[date] = parse_iso_date(args.date) if args.date else None
    facts = Facts(account_id, args.description, args.memo or "", merchant_key(args.description), amount, day)
    explanation = rules.explain_facts(conn, business["id"], facts)
    print(f"Merchant reads as: {merchant_key(args.description)}")
    if args.why:
        _print_trials(conn, explanation)
    rule = explanation.winner
    if rule is None:
        print("No rule matches. This row would wait in the review queue.")
        if not args.amount:
            print("Tested as 1.00 of money out. Rules limited by direction or size need --amount.")
        if day is None and any(c.group in ("date", "day") for t in explanation.trials for c in t.rule.conditions):
            print("Some rules look at the date. Give one with --date.")
        return EXIT_FINDING
    print(f"Rule {rule.id} ({describe_all(rule.conditions, rules.account_names(conn))}) books it to {_category_text(conn, rule.category_id)}.")
    if rule.note:
        print(f"It also notes: {rule.note}")
    return 0


def _pack_format(args, path: Optional[str]) -> str:
    if args.format:
        return args.format
    return "json" if path and Path(path).suffix.lower() == ".json" else "csv"


def cmd_rules_export(args, conn) -> int:
    business = the_business(conn, args)
    if _pack_format(args, args.output) == "json":
        pack = rule_packs.export_pack(conn, business["id"])
        if args.output:
            # JSON goes out as plain UTF-8: a byte order mark helps spreadsheets, and other JSON readers refuse it.
            write_atomic(Path(args.output), pack.encode("utf-8"))
            print(f"Wrote {args.output}")
        else:
            deliver(pack, None)
    else:
        deliver(rules.export_rules(conn, business["id"]), args.output)
    return 0


def cmd_rules_import(args, conn) -> int:
    business = the_business(conn, args)
    if _pack_format(args, args.file) == "json":
        outcome = rule_packs.import_pack(conn, business["id"], Path(args.file))
        booked = rules.apply_rules(conn, business["id"])["categorized"]
        print(
            f"Added {outcome.added} rule(s) to {business['name']}; skipped {outcome.skipped} it already had. "
            f"They booked {booked} queued row(s)."
        )
        return 0
    count = rules.import_rules(conn, business["id"], Path(args.file))
    outcome = rules.apply_rules(conn, business["id"])
    print(f"Added {count} rule(s) to {business['name']}; they booked {outcome['categorized']} queued row(s).")
    return 0


def _condition_flags(p: argparse.ArgumentParser) -> None:
    group = p.add_argument_group("conditions (a row must match every one given)")
    group.add_argument("--contains", metavar="TEXT", help="the description, merchant or memo contains this")
    group.add_argument("--starts-with", "--starts", dest="starts", metavar="TEXT", help="the description starts with this")
    group.add_argument("--exact", metavar="TEXT", help="the whole description, or the merchant name, is exactly this")
    group.add_argument("--regex", metavar="PATTERN", help="a regular expression finds a match in the description or memo")
    group.add_argument("--amount-min", "--min", dest="min", metavar="AMOUNT", help="the size is at least this much")
    group.add_argument("--amount-max", "--max", dest="max", metavar="AMOUNT", help="the size is at most this much (both: between, or equal)")
    group.add_argument("--direction", choices=("in", "out"), help="money in or money out")
    group.add_argument("--account", help="the row is in this account")
    group.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="the row is dated on or after this day")
    group.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="the row is dated on or before this day")
    group.add_argument("--day-from", type=_day_arg, metavar="DAY", help="the day of the month is at least this (1 to 31)")
    group.add_argument("--day-to", type=_day_arg, metavar="DAY", help="the day of the month is at most this; below --day-from wraps the month end")


def register(commands) -> None:
    rule = add_group(commands, "rules", "categorization rules")
    p = add_command(rule, "add", cmd_rules_add, "add a rule and run it over the queue")
    p.add_argument("--category", required=True, help="the category the rule books rows to")
    _condition_flags(p)
    p.add_argument("--note", help="also put this note on the rows the rule books")
    p.add_argument("--priority", type=int, default=100, help="lower numbers are tried first (default 100)")
    p.add_argument("--business")
    p = add_command(rule, "edit", cmd_rules_edit, "change a rule's conditions, category or note")
    p.add_argument("id", type=int)
    _condition_flags(p)
    p.add_argument("--drop", action="append", choices=GROUPS, metavar="GROUP",
                   help=f"remove the rule's condition of this group: {', '.join(GROUPS)}. Can be repeated")
    p.add_argument("--category", help="book to this category instead")
    noting = p.add_mutually_exclusive_group()
    noting.add_argument("--note", help="put this note on the rows the rule books")
    noting.add_argument("--no-note", action="store_true", help="stop putting a note on rows")
    p = add_command(rule, "list", cmd_rules_list, "list rules in the order they are tried, with what each booked")
    p.add_argument("--business")
    p = add_command(rule, "move", cmd_rules_move, "change where a rule comes in the order")
    p.add_argument("id", type=int)
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--before", type=int, metavar="OTHER", help="put it just before this rule")
    where.add_argument("--to", type=int, metavar="N", help="put it at this place in the order, counted from 1")
    p = add_command(rule, "disable", cmd_rules_disable, "keep a rule but stop it booking rows")
    p.add_argument("id", type=int)
    p = add_command(rule, "enable", cmd_rules_enable, "let a disabled rule book rows again")
    p.add_argument("id", type=int)
    p = add_command(rule, "delete", cmd_rules_delete, "delete a rule; booked rows stay booked")
    p.add_argument("id", type=int)
    p = add_command(rule, "apply", cmd_rules_apply, "run the rules over the review queue")
    p.add_argument("--business")
    p.add_argument("--recheck", action="store_true", help="also re-run over rows rules booked earlier")
    p = add_command(rule, "explain", cmd_rules_explain, "show each rule tried on a row, and why it did or did not match")
    p.add_argument("id", type=int, help="a transaction id, as txn list shows it")
    p = add_command(rule, "test", cmd_rules_test, "see which rule a description would hit")
    p.add_argument("description")
    p.add_argument("--amount", help="signed amount, default -1.00")
    p.add_argument("--account")
    p.add_argument("--date", metavar="YYYY-MM-DD", help="the row's date, for rules that look at dates")
    p.add_argument("--memo", help="the row's memo")
    p.add_argument("--why", action="store_true", help="also show every rule tried and each condition")
    p.add_argument("--business")
    p = add_command(rule, "export", cmd_rules_export, "write rules as CSV, or as a JSON rule pack with --format json")
    p.add_argument("--format", choices=("csv", "json"), help="default csv, or json when -o names a .json file")
    p.add_argument("--business")
    p.add_argument("-o", "--output")
    p = add_command(rule, "import", cmd_rules_import, "add rules from a CSV or a JSON rule pack")
    p.add_argument("file")
    p.add_argument("--format", choices=("csv", "json"), help="default csv, or json for a .json file")
    p.add_argument("--business")
