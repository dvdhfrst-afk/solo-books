"""The payee directory: listing payees, showing one, renaming, merging spellings, and the contractor flag."""
from __future__ import annotations

from typing import List

from .. import books, payees, render
from ..money import format_cents, plain_cents
from .common import add_command, add_group, chosen_businesses, deliver, year_arg

PAYEE_CSV_HEADERS = ["business", "payee", "year", "rows", "money_out", "money_in", "contractor", "spellings"]


def cmd_payee_list(args, conn) -> int:
    rows: List[List[str]] = []
    for business in chosen_businesses(conn, args.business):
        for entry in payees.payee_years(conn, business["id"], args.year):
            rows.append(
                [
                    business["name"],
                    entry.payee.name,
                    str(entry.year),
                    str(entry.rows),
                    entry.money_out,
                    entry.money_in,
                    "yes" if entry.payee.contractor else "",
                    str(len(entry.payee.merchants)),
                ]
            )
    if args.format == "csv":
        csv_rows = [[*row[:4], plain_cents(row[4]), plain_cents(row[5]), row[6] or "no", row[7]] for row in rows]
        deliver(render.csv_text(PAYEE_CSV_HEADERS, csv_rows), args.output)
        return 0
    shown = [[*row[:4], format_cents(row[4]), format_cents(row[5]), row[6], row[7]] for row in rows]
    lines = [
        render.table(["Business", "Payee", "Year", "Rows", "Money out", "Money in", "Contractor", "Spellings"], shown, {2, 3, 4, 5, 7}),
        f"{len(rows)} payee-year(s). Money is every row, whatever its category, including rows waiting for review.",
    ]
    deliver("\n".join(lines), args.output)
    return 0


def cmd_payee_show(args, conn) -> int:
    business = books.get_business(conn, args.business)
    payee = payees.find_payee(conn, business["id"], args.name)
    print(f"Payee {payee.name} ({business['name']})")
    print(f"  Contractor: {'yes' if payee.contractor else 'no'}")
    spellings = payees.rows_per_spelling(conn, business["id"], payee)
    print("  Merchant spellings:")
    for merchant in payee.merchants:
        mark = "  (holds the name and flag)" if merchant == payee.key and len(payee.merchants) > 1 else ""
        print(f"    {merchant}: {spellings[merchant]} row(s){mark}")
    years = [entry for entry in payees.payee_years(conn, business["id"]) if entry.payee.key == payee.key]
    if years:
        print(render.table(
            ["Year", "Rows", "Money out", "Money in"],
            [[str(e.year), str(e.rows), format_cents(e.money_out), format_cents(e.money_in)] for e in years],
            {0, 1, 2, 3},
        ))
    recent = payees.recent_rows(conn, business["id"], payee)
    if recent:
        print(f"Latest {len(recent)} row(s):")
        print(render.table(
            ["Id", "Date", "Account", "Amount", "Description", "Category"],
            [
                [
                    str(row["id"]), args.settings.show_date(row["posted_on"]), row["account_name"], format_cents(row["amount_cents"]),
                    row["description"], f"{row['category_code']} {row['category_name']}" if row["category_code"] else "-",
                ]
                for row in recent
            ],
            {0, 3},
        ))
    return 0


def cmd_payee_rename(args, conn) -> int:
    business = books.get_business(conn, args.business)
    payee, name = payees.rename_payee(conn, business["id"], args.name, args.new_name)
    print(f"Payee {payee.name} is now shown as {name}. Its rows keep the names the bank gave them.")
    return 0


def cmd_payee_merge(args, conn) -> int:
    business = books.get_business(conn, args.business)
    outcome = payees.merge_payees(conn, business["id"], args.name, args.into)
    joining, keeping = outcome["from"], outcome["into"]
    print(f"{joining.name} is now part of payee {keeping.name}: {', '.join(joining.merchants)}.")
    if outcome["contractor_carried"]:
        print(f"{joining.name} was flagged as a contractor, so {keeping.name} is now flagged too.")
    print(f"To take it back out: payee unmerge {joining.key!r}")
    return 0


def cmd_payee_unmerge(args, conn) -> int:
    business = books.get_business(conn, args.business)
    outcome = payees.unmerge_payee(conn, business["id"], args.name)
    print(f"{outcome['merchant']} is its own payee again, apart from {outcome['from']}.")
    if outcome["contractor_returned"]:
        print(f"The merge had flagged {outcome['from']} as a contractor, so that flag came off again.")
    return 0


def cmd_payee_set(args, conn) -> int:
    business = books.get_business(conn, args.business)
    payee = payees.set_contractor(conn, business["id"], args.name, args.contractor == "yes")
    state = "flagged as a contractor" if args.contractor == "yes" else "not flagged as a contractor"
    print(f"{payee.name} is {state}. See 'report contractors'.")
    return 0


def register(commands) -> None:
    payee = add_group(commands, "payee", "who money went to and came from, built from the merchant names on rows")
    p = add_command(payee, "list", cmd_payee_list, "list payees with their rows and money per year")
    p.add_argument("--business", help="default: every business")
    p.add_argument("--year", type=year_arg)
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(payee, "show", cmd_payee_show, "one payee: its spellings, its years and its latest rows")
    p.add_argument("name", help="a payee's name or one of its merchant spellings")
    p.add_argument("--business")
    p = add_command(payee, "rename", cmd_payee_rename, "change the name a payee is shown under")
    p.add_argument("name")
    p.add_argument("new_name")
    p.add_argument("--business")
    p = add_command(payee, "merge", cmd_payee_merge, "make the first payee another spelling of the second")
    p.add_argument("name", help="the payee that joins")
    p.add_argument("into", help="the payee that keeps its name")
    p.add_argument("--business")
    p = add_command(payee, "unmerge", cmd_payee_unmerge, "take a merged spelling back out into a payee of its own")
    p.add_argument("name", help="the merchant spelling that was merged")
    p.add_argument("--business")
    p = add_command(payee, "set", cmd_payee_set, "flag a payee as a contractor, for 'report contractors'")
    p.add_argument("name")
    p.add_argument("--contractor", required=True, choices=("yes", "no"))
    p.add_argument("--business")
