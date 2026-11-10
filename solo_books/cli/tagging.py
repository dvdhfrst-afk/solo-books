"""Tags on rows: adding and removing them, and listing, renaming and deleting a business's tags."""
from __future__ import annotations

from .. import books, render, tags
from ..errors import BooksError
from ..money import format_cents, plain_cents
from .common import add_command, add_group, deliver

TAG_CSV_HEADERS = ["business", "tag", "rows", "money_in", "money_out", "net", "waiting_for_review"]


def cmd_tag_add(args, conn) -> int:
    outcome = tags.add_tag(conn, args.tag, args.ids)
    made = " (a new tag)" if outcome["created"] else ""
    print(f"Tagged {outcome['added']} row(s) {outcome['tag']}{made}.")
    if outcome["already"]:
        print(f"{outcome['already']} row(s) already had it.")
    return 0


def cmd_tag_remove(args, conn) -> int:
    outcome = tags.remove_tag(conn, args.tag, args.ids)
    print(f"Took {outcome['tag']} off {outcome['removed']} row(s).")
    if outcome["not_tagged"]:
        print(f"{outcome['not_tagged']} row(s) did not have it.")
    return 0


def cmd_tag_list(args, conn) -> int:
    business_id = books.get_business(conn, args.business)["id"] if args.business else None
    found = tags.list_tags(conn, business_id)
    if args.format == "csv":
        rows = [
            [
                row["business_name"], row["name"], str(row["row_count"]), plain_cents(row["money_in"]),
                plain_cents(row["money_out"]), plain_cents(row["money_in"] - row["money_out"]), str(row["waiting"]),
            ]
            for row in found
        ]
        deliver(render.csv_text(TAG_CSV_HEADERS, rows), args.output)
        return 0
    rows = [
        [
            row["business_name"], row["name"], str(row["row_count"]), format_cents(row["money_in"]),
            format_cents(row["money_out"]), format_cents(row["money_in"] - row["money_out"]),
            str(row["waiting"]) if row["waiting"] else "",
        ]
        for row in found
    ]
    lines = [render.table(["Business", "Tag", "Rows", "Money in", "Money out", "Net", "Waiting"], rows, {2, 3, 4, 5, 6})]
    lines.append(f"{len(found)} tag(s). Money is every tagged row, booked or waiting for review.")
    deliver("\n".join(lines), args.output)
    return 0


def _business_holding(conn, args) -> int:
    """The business named, or else the only one that has a tag of this name."""
    if args.business:
        return books.get_business(conn, args.business)["id"]
    holders = tags.tags_named(conn, args.tag)
    if len(holders) > 1:
        names = ", ".join(row["name"] for row in books.list_businesses(conn) if row["id"] in holders)
        raise BooksError(f"more than one business has a tag {args.tag!r} ({names}). Say which with --business.")
    return next(iter(holders))


def cmd_tag_rename(args, conn) -> int:
    new_name = tags.rename_tag(conn, _business_holding(conn, args), args.tag, args.new_name)
    print(f"Tag {args.tag} is now {new_name}.")
    return 0


def cmd_tag_delete(args, conn) -> int:
    count = tags.delete_tag(conn, _business_holding(conn, args), args.tag)
    print(f"Deleted tag {args.tag}. It came off {count} row(s); the rows themselves are unchanged.")
    return 0


def register(commands) -> None:
    tag = add_group(commands, "tag", "labels on rows, such as a client or a project")
    p = add_command(tag, "add", cmd_tag_add, "put a tag on rows, making the tag if it is new")
    p.add_argument("tag")
    p.add_argument("ids", nargs="+", type=int, help="transaction ids, all from one business")
    p = add_command(tag, "remove", cmd_tag_remove, "take a tag off rows")
    p.add_argument("tag")
    p.add_argument("ids", nargs="+", type=int, help="transaction ids, all from one business")
    p = add_command(tag, "list", cmd_tag_list, "list tags with how many rows carry each and their money")
    p.add_argument("--business")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(tag, "rename", cmd_tag_rename, "rename a tag; the rows keep it")
    p.add_argument("tag")
    p.add_argument("new_name")
    p.add_argument("--business", help="needed only when more than one business has a tag of that name")
    p = add_command(tag, "delete", cmd_tag_delete, "take a tag off every row and forget it")
    p.add_argument("tag")
    p.add_argument("--business", help="needed only when more than one business has a tag of that name")
