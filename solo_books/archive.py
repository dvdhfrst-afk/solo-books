"""The whole books as one JSON document, to move them to another computer or keep them in a form any program reads.

An archive names its archive_version and carries a SHA-256 of its content. Each version this program has written
is described here, with the step that turns its content into the next version's. Reading an older archive runs
those steps in order, so only the latest version is ever loaded. docs/EXPORTS.md describes the format.

Loading keeps every id, so reconciliations, import history, rules and held-back rows point where they did.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .books import ACCOUNT_KINDS, CATEGORY_KINDS, MAX_NAME
from .csvmap import Mapping
from .db import transaction
from .errors import BooksError
from .formats.base import GENERATOR
from .ledger import natural_key
from .money import MAX_CENTS
from .periods import FIRST_YEAR, LAST_YEAR
from .rules import MATCH_TYPES, check_regex

FORMAT_NAME = "solo-books archive"
_LARGEST_ID = 2 ** 63 - 1
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?( ?(Z|UTC|[+-]\d{2}:?\d{2}))?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Field:
    """One column. kind says what a value must be; ref names the table an id must point into."""

    name: str
    kind: str
    optional: bool = False
    choices: Tuple[str, ...] = ()
    ref: str = ""


@dataclass(frozen=True)
class TableSpec:
    name: str
    fields: Tuple[Field, ...]
    key: Tuple[str, ...] = ("id",)

    @property
    def columns(self) -> List[str]:
        return [f.name for f in self.fields]


@dataclass(frozen=True)
class ArchiveVersion:
    """What one archive version holds, and the step that turns its content into the next version's."""

    number: int
    tables: Tuple[TableSpec, ...]
    upgrade: Optional[Callable[[dict], dict]] = None


def _stamp(name: str, optional: bool = False) -> Field:
    return Field(name, "stamp", optional)


def _ref(name: str, table: str, optional: bool = False) -> Field:
    return Field(name, "ref", optional, ref=table)


_ID = Field("id", "id")

TABLES_V1 = (
    TableSpec("businesses", (_ID, Field("name", "label"), Field("activity", "text"), _stamp("created_at"))),
    TableSpec("categories", (
        _ID, _ref("business_id", "businesses"), Field("code", "name"), Field("name", "label"),
        Field("kind", "choice", choices=CATEGORY_KINDS), Field("schedule_c_key", "text", True), Field("active", "flag"),
    )),
    TableSpec("accounts", (
        _ID, _ref("business_id", "businesses"), Field("name", "label"), Field("kind", "choice", choices=ACCOUNT_KINDS),
        Field("institution", "text"), Field("opening_balance_cents", "cents", True), Field("opening_date", "date", True),
        Field("mapping_json", "mapping", True), _stamp("created_at"),
    )),
    TableSpec("imports", (
        _ID, _ref("account_id", "accounts"), Field("file_name", "text"), Field("file_sha256", "text"),
        _stamp("imported_at"), Field("rows_read", "count"), Field("rows_new", "count"),
        Field("rows_duplicate", "count"), Field("rows_skipped", "count"), Field("mapping_json", "json"),
        _stamp("undone_at", True),
    )),
    TableSpec("rules", (
        _ID, _ref("business_id", "businesses"), _ref("account_id", "accounts", True), Field("priority", "int"),
        Field("match_type", "choice", choices=MATCH_TYPES), Field("pattern", "name"),
        Field("direction", "choice", True, ("in", "out")), Field("min_cents", "cents", True),
        Field("max_cents", "cents", True), _ref("category_id", "categories"), _stamp("created_at"),
    )),
    TableSpec("reconciliations", (
        _ID, _ref("account_id", "accounts"), Field("period_start", "date"), Field("period_end", "date"),
        Field("statement_begin_cents", "cents"), Field("statement_end_cents", "cents"),
        Field("status", "choice", choices=("open", "finished")), _stamp("created_at"), _stamp("finished_at", True),
    )),
    TableSpec("transactions", (
        _ID, _ref("account_id", "accounts"), Field("posted_on", "date"), Field("amount_cents", "cents"),
        Field("description", "name"), Field("memo", "text"), Field("reference", "text"), Field("merchant", "text"),
        Field("dedupe_key", "name"), Field("natural_key", "name"), Field("occurrence", "count"),
        _ref("import_id", "imports", True), _ref("category_id", "categories", True),
        Field("category_source", "choice", True, ("rule", "manual")), _ref("rule_id", "rules", True),
        _ref("reconciliation_id", "reconciliations", True), Field("note", "text"), _stamp("created_at"),
    )),
    TableSpec("import_rows", (_ref("import_id", "imports"), _ref("transaction_id", "transactions")), ("import_id", "transaction_id")),
    TableSpec("rule_exceptions", (_ref("rule_id", "rules"), _ref("transaction_id", "transactions")), ("rule_id", "transaction_id")),
    TableSpec("deleted_rows", (
        _ref("account_id", "accounts"), Field("dedupe_key", "name"), Field("natural_key", "name"),
        Field("occurrence", "count"), _stamp("deleted_at"),
    ), ("account_id", "dedupe_key", "occurrence")),
    TableSpec("inventory_counts", (
        _ref("business_id", "businesses"), Field("tax_year", "year"), Field("beginning_cents", "cents"),
        Field("ending_cents", "cents"),
    ), ("business_id", "tax_year")),
    TableSpec("meta", (Field("key", "name"), Field("value", "text")), ("key",)),
)

# Every version this program can read. A new version adds its entry here and gives the one before it an upgrade.
VERSIONS: Dict[int, ArchiveVersion] = {1: ArchiveVersion(1, TABLES_V1)}


def current_version() -> int:
    return max(VERSIONS)


def _tables() -> Tuple[TableSpec, ...]:
    return VERSIONS[current_version()].tables


def digest(content: dict) -> str:
    """SHA-256 of the content written canonically: keys sorted, no spaces, non-ASCII characters as themselves."""
    text = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Writing

def read_content(conn: sqlite3.Connection) -> dict:
    content = {}
    for spec in _tables():
        rows = conn.execute(f"SELECT {', '.join(spec.columns)} FROM {spec.name} ORDER BY {', '.join(spec.key)}").fetchall()
        content[spec.name] = [{column: row[column] for column in spec.columns} for row in rows]
    return content


def export_archive(conn: sqlite3.Connection) -> Tuple[str, Dict[str, int]]:
    """The archive's text, and how many rows each table holds. One row per line, so files compare line by line."""
    content = read_content(conn)
    head = {
        "archive_version": current_version(),
        "format": FORMAT_NAME,
        "written_by": GENERATOR,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": digest(content),
    }
    lines = ["{"]
    lines.extend(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}," for key, value in head.items())
    lines.append('  "content": {')
    for index, (table, rows) in enumerate(content.items()):
        end = "," if index < len(content) - 1 else ""
        if not rows:
            lines.append(f"    {json.dumps(table)}: []{end}")
            continue
        lines.append(f"    {json.dumps(table)}: [")
        lines.extend(
            f"      {json.dumps(row, ensure_ascii=False)}{',' if number < len(rows) - 1 else ''}"
            for number, row in enumerate(rows)
        )
        lines.append(f"    ]{end}")
    lines.extend(["  }", "}", ""])
    return "\n".join(lines), {table: len(rows) for table, rows in content.items()}


# Reading

def _where(table: str, number: int, row: dict) -> str:
    ident = row.get("id")
    named = f" (id {ident})" if isinstance(ident, int) and not isinstance(ident, bool) else ""
    return f"the archive's {table} entry {number}{named}"


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_date(value) -> bool:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return False
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return False
    return FIRST_YEAR <= day.year <= LAST_YEAR


def _problem(spec: Field, value) -> Optional[str]:
    """What is wrong with one value, as the end of a sentence, or None when it is fine."""
    kind = spec.kind
    if value is None:
        return None if spec.optional else "is empty, and it must have a value"
    if kind in ("id", "ref"):
        return None if _is_int(value) and 1 <= value <= _LARGEST_ID else f"must be a whole number from 1 up, not {value!r}"
    if kind in ("int", "count", "flag", "year", "cents") and not _is_int(value):
        return f"must be a whole number, not {value!r}"
    if kind == "count" and value < 0:
        return f"cannot be below zero: {value}"
    if kind == "int" and abs(value) > _LARGEST_ID:
        return f"is too large: {value}"
    if kind == "flag" and value not in (0, 1):
        return f"must be 0 or 1, not {value!r}"
    if kind == "year" and not FIRST_YEAR <= value <= LAST_YEAR:
        return f"must be a year from {FIRST_YEAR} to {LAST_YEAR}, not {value}"
    if kind == "cents" and abs(value) > MAX_CENTS:
        return f"is too large to be a real amount: {value}"
    if kind in ("text", "name", "label", "choice", "date", "stamp", "json", "mapping") and not isinstance(value, str):
        return f"must be text, not {value!r}"
    if kind in ("name", "label") and not value.strip():
        return "must not be blank"
    if kind == "label" and len(value) > MAX_NAME:
        return f"is longer than {MAX_NAME} characters"
    if kind == "choice" and value not in spec.choices:
        return f"must be one of {', '.join(spec.choices)}, not {value!r}"
    if kind == "date" and not _is_date(value):
        return f"must be a date written YYYY-MM-DD, not {value!r}"
    if kind == "stamp" and (not _STAMP.match(value) or not _is_date(value[:10])):
        return f"must be a date and time, not {value!r}"
    if kind in ("json", "mapping"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return "must hold a JSON object, and it is not valid JSON"
        if not isinstance(parsed, dict):
            return "must hold a JSON object"
        if kind == "mapping":
            try:
                Mapping.from_json(value)
            except BooksError as exc:
                return f"is not a usable column mapping ({exc})"
    return None


def validate(content, version: ArchiveVersion) -> None:
    """Every table, every row and every field, stopping at the first thing that is wrong."""
    if not isinstance(content, dict):
        raise BooksError("the archive's content must be a JSON object of tables")
    names = [spec.name for spec in version.tables]
    for name in content:
        if name not in names:
            raise BooksError(f"the archive holds a table {name!r}, which archive version {version.number} does not have")
    seen: Dict[str, set] = {}
    for spec in version.tables:
        rows = content.get(spec.name)
        if not isinstance(rows, list):
            raise BooksError(f"the archive has no list of {spec.name}")
        keys = set()
        for number, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise BooksError(f"the archive's {spec.name} entry {number} is not a JSON object")
            where = _where(spec.name, number, row)
            for column in row:
                if column not in spec.columns:
                    raise BooksError(f"{where} has a field {column!r}, which archive version {version.number} does not have")
            for field_spec in spec.fields:
                if field_spec.name not in row:
                    raise BooksError(f"{where} has no {field_spec.name}")
                value = row[field_spec.name]
                problem = _problem(field_spec, value)
                if problem:
                    raise BooksError(f"{where}: {field_spec.name} {problem}.")
                if field_spec.kind == "ref" and value is not None and value not in seen[field_spec.ref]:
                    raise BooksError(f"{where}: {field_spec.name} {value} points at no entry in {field_spec.ref}.")
            key = tuple(row[column] for column in spec.key)
            if key in keys:
                raise BooksError(f"{where} repeats {', '.join(spec.key)} {', '.join(str(k) for k in key)}")
            keys.add(key)
        seen[spec.name] = {key[0] for key in keys} if spec.key == ("id",) else set()


def _by_id(content: dict, table: str) -> Dict[int, dict]:
    return {row["id"]: row for row in content[table]}


def check_links(content: dict) -> None:
    """What the books themselves rely on between tables: rows booked inside their own business, and the rest."""
    categories, accounts = _by_id(content, "categories"), _by_id(content, "accounts")
    rules, imports, recons = _by_id(content, "rules"), _by_id(content, "imports"), _by_id(content, "reconciliations")
    for number, row in enumerate(content["accounts"], start=1):
        if (row["opening_balance_cents"] is None) != (row["opening_date"] is None):
            raise BooksError(f"{_where('accounts', number, row)} has an opening balance without a date, or a date without one.")
    for number, row in enumerate(content["rules"], start=1):
        where = _where("rules", number, row)
        if categories[row["category_id"]]["business_id"] != row["business_id"]:
            raise BooksError(f"{where} books to another business's category.")
        if row["account_id"] is not None and accounts[row["account_id"]]["business_id"] != row["business_id"]:
            raise BooksError(f"{where} is limited to another business's account.")
        if row["match_type"] == "regex":
            try:
                check_regex(row["pattern"])
            except BooksError as exc:
                raise BooksError(f"{where}: {exc}") from None
    for number, row in enumerate(content["reconciliations"], start=1):
        if row["period_end"] < row["period_start"]:
            raise BooksError(f"{_where('reconciliations', number, row)} ends before it starts.")
    for number, row in enumerate(content["transactions"], start=1):
        where = _where("transactions", number, row)
        business_id = accounts[row["account_id"]]["business_id"]
        if row["category_id"] is not None and categories[row["category_id"]]["business_id"] != business_id:
            raise BooksError(f"{where} is booked to another business's category.")
        if (row["category_id"] is None) != (row["category_source"] is None):
            raise BooksError(f"{where} has a category without saying how it was booked, or the other way round.")
        if row["rule_id"] is not None and rules[row["rule_id"]]["business_id"] != business_id:
            raise BooksError(f"{where} names another business's rule.")
        if row["import_id"] is not None and imports[row["import_id"]]["account_id"] != row["account_id"]:
            raise BooksError(f"{where} names an import into another account.")
        if row["reconciliation_id"] is not None:
            recon = recons[row["reconciliation_id"]]
            covered = recon["period_start"] <= row["posted_on"] <= recon["period_end"]
            if recon["status"] != "finished" or recon["account_id"] != row["account_id"] or not covered:
                raise BooksError(f"{where} is locked to a reconciliation that does not cover it.")
        if row["natural_key"] != natural_key(date.fromisoformat(row["posted_on"]), row["amount_cents"], row["description"]):
            raise BooksError(f"{where}: natural_key does not match its date, amount and description.")
    transactions = _by_id(content, "transactions")
    for number, row in enumerate(content["import_rows"], start=1):
        if imports[row["import_id"]]["account_id"] != transactions[row["transaction_id"]]["account_id"]:
            raise BooksError(f"the archive's import_rows entry {number} ties a row to an import into another account.")


def read_archive(path: Path) -> Tuple[dict, int]:
    """The archive's content at the current version, checked from end to end, and the version it was written as."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise BooksError(f"cannot read {path}: {exc.strerror}") from None
    except UnicodeDecodeError:
        raise BooksError(f"{path} is not a solo-books archive: it is not UTF-8 text.") from None
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise BooksError(f"{path} is not a solo-books archive: it is not valid JSON ({getattr(exc, 'msg', exc)}).") from None
    except RecursionError:
        raise BooksError(f"{path} is not a solo-books archive: it nests too deeply.") from None
    if not isinstance(document, dict) or document.get("format") != FORMAT_NAME:
        raise BooksError(f"{path} is not a solo-books archive.")
    version = document.get("archive_version")
    if not _is_int(version):
        raise BooksError(f"{path} does not say which archive version it is.")
    newest = current_version()
    if version > newest:
        raise BooksError(
            f"{path} is archive version {version}, from a newer solo-books. This one reads archive versions up to "
            f"{newest}. Install the newer version to load it."
        )
    if version not in VERSIONS:
        raise BooksError(f"{path} says it is archive version {version}, which no solo-books has written.")
    content, recorded = document.get("content"), document.get("sha256")
    if not isinstance(recorded, str) or not _SHA256.match(recorded):
        raise BooksError(f"{path} carries no SHA-256 of its content, so it cannot be checked.")
    if digest(content) != recorded:
        raise BooksError(
            f"{path} does not match the SHA-256 it carries, so it was changed or damaged after it was written. "
            "Nothing was loaded."
        )
    validate(content, VERSIONS[version])
    read_as = version
    while version < newest:
        content = VERSIONS[version].upgrade(content)
        version += 1
        validate(content, VERSIONS[version])
    check_links(content)
    return content, read_as


def require_empty(conn: sqlite3.Connection) -> None:
    held = {
        spec.name: conn.execute(f"SELECT COUNT(*) FROM {spec.name}").fetchone()[0] for spec in _tables() if spec.name != "meta"
    }
    if any(held.values()):
        raise BooksError(
            f"these books already hold {held['businesses']} business(es) and {held['transactions']} row(s). An archive "
            "loads only into new, empty books, so nothing was changed. Point --db at a new file."
        )


def import_archive(conn: sqlite3.Connection, path: Path) -> Tuple[Dict[str, int], int]:
    """Load an archive into empty books. All of it goes in, or none of it does."""
    require_empty(conn)
    content, read_as = read_archive(path)
    try:
        with transaction(conn):
            conn.execute("DELETE FROM meta")
            for spec in _tables():
                columns = spec.columns
                conn.executemany(
                    f"INSERT INTO {spec.name} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    ([row[column] for column in columns] for row in content[spec.name]),
                )
    except sqlite3.IntegrityError as exc:
        raise BooksError(f"the archive breaks a rule the books keep ({exc}). Nothing was loaded.") from None
    return {spec.name: len(content[spec.name]) for spec in _tables()}, read_as
