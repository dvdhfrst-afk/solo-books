"""Budgets per category: setting, listing, deleting, and copying a year's plan to another year."""
from __future__ import annotations

import argparse

from .. import books, budgets, render
from ..errors import BooksError
from ..money import format_cents, parse_cents, plain_cents
from .common import add_command, add_group, chosen_businesses, deliver, year_arg

BUDGET_CSV_HEADERS = ["business", "year", "code", "category", "kind", "entered_as", *[f"month_{m:02d}" for m in range(1, 13)], "year_total"]


def _month_arg(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a month number: {text!r}") from None
    if not 1 <= value <= 12:
        raise argparse.ArgumentTypeError(f"a month is 1 to 12: {text!r}")
    return value


def cmd_budget_set(args, conn) -> int:
    business = books.get_business(conn, args.business)
    category = books.find_category(conn, business["id"], args.category)
    outcome = budgets.set_budget(
        conn,
        business,
        category,
        args.year,
        monthly=parse_cents(args.monthly) if args.monthly is not None else None,
        yearly=parse_cents(args.yearly) if args.yearly is not None else None,
        month=args.month,
    )
    if outcome["spread"]:
        print("The yearly amount was spread over the twelve months first, so the other months keep their share.")
    print(f"Budget saved for {category['code']} {category['name']}, {args.year}: {format_cents(outcome['year_total'])} for the year.")
    return 0


def cmd_budget_list(args, conn) -> int:
    business_id = books.get_business(conn, args.business)["id"] if args.business else None
    found = budgets.list_budgets(conn, business_id, args.year)
    if args.format == "csv":
        rows = [
            [
                entry.business, str(entry.year), entry.code, entry.name, entry.kind, "yearly" if entry.yearly else "monthly",
                *[plain_cents(entry.months[m]) for m in range(1, 13)], plain_cents(entry.total),
            ]
            for entry in found
        ]
        deliver(render.csv_text(BUDGET_CSV_HEADERS, rows), args.output)
        return 0
    rows = []
    for entry in found:
        if entry.yearly:
            how = "yearly"
        elif entry.same_every_month is not None:
            how = f"{format_cents(entry.same_every_month)} a month"
        else:
            how = "by month"
        rows.append([entry.business, str(entry.year), f"{entry.code} {entry.name}", entry.kind, how, format_cents(entry.total)])
    lines = [
        render.table(["Business", "Year", "Category", "Kind", "Entered", "Year total"], rows, {1, 5}),
        f"{len(found)} budget(s). Amounts are positive for income and for spending alike.",
    ]
    deliver("\n".join(lines), args.output)
    return 0


def cmd_budget_delete(args, conn) -> int:
    business = books.get_business(conn, args.business)
    category = books.find_category(conn, business["id"], args.category)
    total = budgets.delete_budget(conn, business, category, args.year)
    print(f"Deleted the {args.year} budget for {category['code']} {category['name']} ({format_cents(total)} for the year).")
    return 0


def cmd_budget_copy(args, conn) -> int:
    chosen = chosen_businesses(conn, args.business)
    if args.business is None:
        chosen = [business for business in chosen if budgets.list_budgets(conn, business["id"], args.source)]
        if not chosen:
            raise BooksError(f"no business has a budget for {args.source} to copy")
    for business in chosen:
        count = budgets.copy_budgets(conn, business, args.source, args.target, replace=args.replace)
        print(f"{business['name']}: copied {count} category budget(s) from {args.source} to {args.target}.")
    return 0


def register(commands) -> None:
    budget = add_group(commands, "budget", "planned amounts per category, by year or by month")
    p = add_command(budget, "set", cmd_budget_set, "set a category's budget for a year, every month, or one month")
    p.add_argument("--business")
    p.add_argument("--category", required=True, help="category code or name")
    p.add_argument("--year", type=year_arg, required=True)
    amount = p.add_mutually_exclusive_group(required=True)
    amount.add_argument("--monthly", help="the same amount each month, or with --month, that month's amount")
    amount.add_argument("--yearly", help="one amount for the whole year, spread evenly over its months in reports")
    p.add_argument("--month", type=_month_arg, help="1 to 12: set only this month (needs --monthly)")
    p = add_command(budget, "list", cmd_budget_list, "list budgets")
    p.add_argument("--business")
    p.add_argument("--year", type=year_arg)
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(budget, "delete", cmd_budget_delete, "delete a category's budget for a year")
    p.add_argument("--business")
    p.add_argument("--category", required=True)
    p.add_argument("--year", type=year_arg, required=True)
    p = add_command(budget, "copy", cmd_budget_copy, "copy a year's budgets to another year")
    p.add_argument("--from", dest="source", type=year_arg, required=True)
    p.add_argument("--to", dest="target", type=year_arg, required=True)
    p.add_argument("--business", help="default: every business with a budget for that year")
    p.add_argument("--replace", action="store_true", help="overwrite budgets already set for the target year")
