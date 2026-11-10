"""The history of changes: listing them, showing one, and taking one back."""
from __future__ import annotations

from typing import List

from .. import books, history, render
from ..money import format_cents
from ..periods import parse_period
from .common import add_command, add_group, deliver, positive_arg

_DEFAULT_LIMIT = 50
_LONGEST_VALUE = 200
HISTORY_CSV_HEADERS = ["id", "changed_at_utc", "command", "kind", "business", "rows", "summary", "taken_back_by"]


def _business_cell(entry) -> str:
    if entry["business_id"] is None:
        return ""
    return entry["business_name"] or f"(deleted business {entry['business_id']})"


def cmd_history_list(args, conn) -> int:
    business_id = books.get_business(conn, args.business)["id"] if args.business else None
    limit = None if args.all else args.limit
    entries = history.list_entries(
        conn,
        period=parse_period(args.period) if args.period else None,
        kind=args.kind,
        transaction_id=args.row,
        business_id=business_id,
        limit=limit,
    )
    if args.format == "csv":
        rows = [
            [
                str(entry["id"]),
                entry["changed_at"],
                entry["source"],
                entry["kind"],
                _business_cell(entry),
                str(entry["row_count"]),
                entry["summary"],
                str(entry["reverted_by"] or ""),
            ]
            for entry in entries
        ]
        deliver(render.csv_text(HISTORY_CSV_HEADERS, rows), args.output)
        return 0
    rows = [
        [
            str(entry["id"]),
            entry["changed_at"],
            entry["source"] or "-",
            entry["kind"],
            _business_cell(entry),
            str(entry["row_count"]),
            entry["summary"] + (f" [taken back by {entry['reverted_by']}]" if entry["reverted_by"] else ""),
        ]
        for entry in entries
    ]
    lines = [render.table(["Id", "When (UTC)", "Command", "Kind", "Business", "Rows", "What changed"], rows, {0, 5}, max_cell=90)]
    if limit is not None and len(entries) == limit:
        lines.append(f"The latest {limit} change(s), newest first. Use --limit or --all to see more.")
    else:
        lines.append(f"{len(entries)} change(s), newest first.")
    deliver("\n".join(lines), args.output)
    return 0


def _value(key: str, value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_value(k, v)}" for k, v in sorted(value.items())) + "}"
    if isinstance(value, list):
        shown = ", ".join(str(item) for item in value[:20])
        return f"{shown}, ...and {len(value) - 20} more" if len(value) > 20 else shown
    if isinstance(value, int) and not isinstance(value, bool) and key.endswith("cents"):
        return format_cents(value)
    if value is None:
        return "none"
    text = " ".join(str(value).split())
    return text if len(text) <= _LONGEST_VALUE else text[: _LONGEST_VALUE - 3] + "..."


def detail_lines(detail: dict) -> List[str]:
    return [f"  {key}: {_value(key, value)}" for key, value in sorted(detail.items())]


def cmd_history_show(args, conn) -> int:
    entry = history.get_entry(conn, args.id)
    lines = [
        f"Change {entry['id']}: {entry['summary']}",
        f"  When:     {entry['changed_at']} UTC",
        f"  Command:  {entry['source'] or 'not recorded'}",
        f"  Kind:     {entry['kind']} ({history.KINDS.get(entry['kind'], 'a kind this version does not know')})",
    ]
    if entry["business_id"] is not None:
        lines.append(f"  Business: {_business_cell(entry)}")
    if entry["reverted_by"]:
        lines.append(f"  Taken back by change {entry['reverted_by']}.")
    elif entry["kind"] in history.REVERTIBLE:
        lines.append(f"  This change can be taken back with: history revert {entry['id']}")
    detail = history.entry_detail(entry)
    if detail:
        lines.append("Details:")
        lines.extend(detail_lines(detail))
    changes = history.entry_rows(conn, entry["id"])
    if changes:
        lines.append(f"Rows ({len(changes)}):")
        table_rows = [[str(row_id), history.describe(before), history.describe(after)] for row_id, before, after in changes]
        lines.append(render.table(["Transaction", "Before", "After"], table_rows, {0}, max_cell=70))
    deliver("\n".join(lines), args.output)
    return 0


def cmd_history_revert(args, conn) -> int:
    new_id, summary = history.revert(conn, args.id)
    print(f"Took back change {args.id}: {summary}")
    print(f"That is recorded as change {new_id}.")
    return 0


def register(commands) -> None:
    log = add_group(commands, "history", "every change made to the books, and taking some of them back")
    p = add_command(log, "list", cmd_history_list, "list changes, newest first")
    p.add_argument("--period", help="when the change was made: 2025, 2025-Q2, 2025-03 or 2025-01-15..2025-02-20 (UTC)")
    p.add_argument("--kind", help="a kind such as categorize or rule-add, or a first word such as rule")
    p.add_argument("--row", type=positive_arg, metavar="ID", help="only changes that touched this transaction")
    p.add_argument("--business")
    p.add_argument("--limit", type=positive_arg, default=_DEFAULT_LIMIT, help=f"how many to show (default {_DEFAULT_LIMIT})")
    p.add_argument("--all", action="store_true", help="show every change")
    p.add_argument("--format", choices=("table", "csv"), default="table")
    p.add_argument("-o", "--output")
    p = add_command(log, "show", cmd_history_show, "everything one change recorded, with each row before and after")
    p.add_argument("id", type=int)
    p.add_argument("-o", "--output")
    p = add_command(
        log, "revert", cmd_history_revert,
        "take back one change: " + ", ".join(history.REVERTIBLE) + ". The taking back is recorded too",
    )
    p.add_argument("id", type=int)
