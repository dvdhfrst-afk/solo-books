"""The SQLite file: opening it, its schema, and moving it forward between versions."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from .errors import BooksError
from .files import SQLITE_MAGIC

DEFAULT_DB_NAME = "books.sqlite3"
DB_ENV_VAR = "SOLO_BOOKS_DB"
# Marks a file as solo-books in the SQLite header ("SOLO"), so another program's database is never touched.
APPLICATION_ID = 0x534F4C4F
BUSY_SECONDS = 10
RECOVERY_DOC = "docs/RECOVERY.md"

# Rules made of conditions. Kept apart so salvage can reuse the statements that turn old rules into conditions.
_RULE_CONDITIONS_MIGRATION = """
-- A rule is a list of conditions, all of which must match, and its actions: the category it books and,
-- if it has one, the note it puts on the rows it books. The rules table's match_type, pattern, direction,
-- min_cents, max_cents and account_id are what earlier schemas used. They are no longer read; match_type and
-- pattern still get the rule's wording, if it has any, because they cannot be empty.
CREATE TABLE rule_conditions (
    id          INTEGER PRIMARY KEY,
    rule_id     INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('contains', 'starts_with', 'exact', 'regex', 'amount_equals',
                    'amount_at_least', 'amount_at_most', 'amount_between', 'direction', 'account',
                    'date_between', 'day_of_month_between')),
    -- Wording, or 'in' or 'out' for a direction.
    text        TEXT,
    -- Cents for an amount, a day of the month, or a date written as YYYYMMDD.
    low         INTEGER CHECK (low IS NULL OR typeof(low) = 'integer'),
    high        INTEGER CHECK (high IS NULL OR typeof(high) = 'integer'),
    account_id  INTEGER REFERENCES accounts(id) ON DELETE CASCADE
);
CREATE INDEX rule_conditions_by_rule ON rule_conditions (rule_id);

ALTER TABLE rules ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1));
ALTER TABLE rules ADD COLUMN note TEXT NOT NULL DEFAULT '';
-- 1 when a rule wrote the note, so a later rule run may replace it. A note a person wrote is never touched.
ALTER TABLE transactions ADD COLUMN note_from_rule INTEGER NOT NULL DEFAULT 0 CHECK (note_from_rule IN (0, 1));

-- Every existing rule keeps its id, its place in the order and what it matches.
INSERT INTO rule_conditions (rule_id, kind, text)
    SELECT id, CASE match_type WHEN 'starts' THEN 'starts_with' ELSE match_type END, pattern FROM rules ORDER BY id;
INSERT INTO rule_conditions (rule_id, kind, text)
    SELECT id, 'direction', direction FROM rules WHERE direction IS NOT NULL ORDER BY id;
INSERT INTO rule_conditions (rule_id, kind, low, high)
    SELECT id,
           CASE WHEN min_cents IS NOT NULL AND max_cents IS NOT NULL AND min_cents = max_cents THEN 'amount_equals'
                WHEN min_cents IS NOT NULL AND max_cents IS NOT NULL THEN 'amount_between'
                WHEN min_cents IS NOT NULL THEN 'amount_at_least'
                ELSE 'amount_at_most' END,
           min_cents, max_cents
    FROM rules WHERE min_cents IS NOT NULL OR max_cents IS NOT NULL ORDER BY id;
INSERT INTO rule_conditions (rule_id, kind, account_id)
    SELECT id, 'account', account_id FROM rules WHERE account_id IS NOT NULL ORDER BY id;

-- A rule limited to an account goes with the account, as it always has.
CREATE TRIGGER rules_leave_with_their_account BEFORE DELETE ON accounts
BEGIN
    DELETE FROM rules WHERE id IN (SELECT rule_id FROM rule_conditions WHERE kind = 'account' AND account_id = OLD.id);
END;
"""

# Each entry moves the file forward one version. PRAGMA user_version records how far it has come.
# Append new entries; never edit one that has shipped.
MIGRATIONS = [
    """
    CREATE TABLE businesses (
        id          INTEGER PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
        activity    TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- The chart of accounts for one business: everything a bank row can be booked to.
    -- schedule_c_key is a stable name for a form line. Line numbers move between
    -- tax years, so each year's data file says which number the key has that year.
    CREATE TABLE categories (
        id              INTEGER PRIMARY KEY,
        business_id     INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        code            TEXT NOT NULL,
        name            TEXT NOT NULL COLLATE NOCASE,
        kind            TEXT NOT NULL CHECK (kind IN
                            ('income', 'returns', 'cogs', 'expense', 'transfer', 'equity')),
        schedule_c_key  TEXT,
        active          INTEGER NOT NULL DEFAULT 1,
        UNIQUE (business_id, code),
        UNIQUE (business_id, name)
    );

    -- Bank and card accounts. Each belongs to exactly one business.
    CREATE TABLE accounts (
        id                     INTEGER PRIMARY KEY,
        business_id            INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        name                   TEXT NOT NULL UNIQUE COLLATE NOCASE,
        kind                   TEXT NOT NULL CHECK (kind IN ('checking', 'savings', 'credit_card', 'other')),
        institution            TEXT NOT NULL DEFAULT '',
        opening_balance_cents  INTEGER CHECK (opening_balance_cents IS NULL
                                              OR typeof(opening_balance_cents) = 'integer'),
        opening_date           TEXT,
        mapping_json           TEXT,
        created_at             TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE imports (
        id              INTEGER PRIMARY KEY,
        account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        file_name       TEXT NOT NULL,
        file_sha256     TEXT NOT NULL,
        imported_at     TEXT NOT NULL DEFAULT (datetime('now')),
        rows_read       INTEGER NOT NULL,
        rows_new        INTEGER NOT NULL,
        rows_duplicate  INTEGER NOT NULL,
        rows_skipped    INTEGER NOT NULL,
        mapping_json    TEXT NOT NULL,
        undone_at       TEXT
    );

    CREATE TABLE rules (
        id           INTEGER PRIMARY KEY,
        business_id  INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        account_id   INTEGER REFERENCES accounts(id) ON DELETE CASCADE,
        priority     INTEGER NOT NULL DEFAULT 100,
        match_type   TEXT NOT NULL CHECK (match_type IN ('contains', 'starts', 'exact', 'regex')),
        pattern      TEXT NOT NULL,
        direction    TEXT CHECK (direction IN ('in', 'out')),
        min_cents    INTEGER,
        max_cents    INTEGER,
        category_id  INTEGER NOT NULL REFERENCES categories(id),
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE reconciliations (
        id                     INTEGER PRIMARY KEY,
        account_id             INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        period_start           TEXT NOT NULL,
        period_end             TEXT NOT NULL,
        statement_begin_cents  INTEGER NOT NULL CHECK (typeof(statement_begin_cents) = 'integer'),
        statement_end_cents    INTEGER NOT NULL CHECK (typeof(statement_end_cents) = 'integer'),
        status                 TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'finished')),
        created_at             TEXT NOT NULL DEFAULT (datetime('now')),
        finished_at            TEXT,
        UNIQUE (account_id, period_start, period_end)
    );

    -- amount_cents is signed from the account's point of view: positive puts money in
    -- (or pays down a card), negative takes money out (or adds to what a card is owed).
    -- dedupe_key plus occurrence identify a bank row across overlapping exports. natural_key is
    -- the date, amount and text, which still matches when a bank id is added or dropped later.
    CREATE TABLE transactions (
        id                 INTEGER PRIMARY KEY,
        account_id         INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        posted_on          TEXT NOT NULL,
        amount_cents       INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer'),
        description        TEXT NOT NULL,
        memo               TEXT NOT NULL DEFAULT '',
        reference          TEXT NOT NULL DEFAULT '',
        merchant           TEXT NOT NULL,
        dedupe_key         TEXT NOT NULL,
        natural_key        TEXT NOT NULL,
        occurrence         INTEGER NOT NULL,
        import_id          INTEGER REFERENCES imports(id) ON DELETE SET NULL,
        category_id        INTEGER REFERENCES categories(id),
        category_source    TEXT CHECK (category_source IN ('rule', 'manual')),
        rule_id            INTEGER REFERENCES rules(id) ON DELETE SET NULL,
        reconciliation_id  INTEGER REFERENCES reconciliations(id) ON DELETE SET NULL,
        note               TEXT NOT NULL DEFAULT '',
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (account_id, dedupe_key, occurrence)
    );
    CREATE INDEX transactions_by_date ON transactions (account_id, posted_on);
    CREATE INDEX transactions_by_category ON transactions (category_id, posted_on);
    CREATE INDEX transactions_by_natural_key ON transactions (account_id, natural_key);

    -- Every live import whose file held a row, not only the one that first added it.
    CREATE TABLE import_rows (
        import_id       INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
        transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        PRIMARY KEY (import_id, transaction_id)
    );
    CREATE INDEX import_rows_by_transaction ON import_rows (transaction_id);

    -- Rows a person held back when making a rule. That rule never books them.
    CREATE TABLE rule_exceptions (
        rule_id         INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
        transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        PRIMARY KEY (rule_id, transaction_id)
    );

    -- Bank rows someone deleted on purpose, so a later overlapping export does not bring them back.
    CREATE TABLE deleted_rows (
        account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        dedupe_key  TEXT NOT NULL,
        natural_key TEXT NOT NULL,
        occurrence  INTEGER NOT NULL,
        deleted_at  TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (account_id, dedupe_key, occurrence)
    );

    CREATE TABLE inventory_counts (
        business_id      INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        tax_year         INTEGER NOT NULL,
        beginning_cents  INTEGER NOT NULL CHECK (typeof(beginning_cents) = 'integer'),
        ending_cents     INTEGER NOT NULL CHECK (typeof(ending_cents) = 'integer'),
        PRIMARY KEY (business_id, tax_year)
    );
    """,
    """
    CREATE TABLE meta (
        key    TEXT PRIMARY KEY,
        value  TEXT NOT NULL
    );
    PRAGMA application_id = 1397705807;
    CREATE INDEX transactions_by_amount ON transactions (account_id, amount_cents, posted_on);
    """,
    """
    -- Every change to the books. What a change touched is named by plain numbers and copied values,
    -- not by links, so the record outlives the rows, rules and names it describes.
    CREATE TABLE history (
        id           INTEGER PRIMARY KEY,
        changed_at   TEXT NOT NULL DEFAULT (datetime('now')),
        source       TEXT NOT NULL DEFAULT '',
        kind         TEXT NOT NULL,
        business_id  INTEGER,
        summary      TEXT NOT NULL,
        detail_json  TEXT NOT NULL DEFAULT '{}',
        reverted_by  INTEGER
    );
    CREATE INDEX history_by_time ON history (changed_at);
    -- One bank row a change touched, with what mattered about it before and after.
    CREATE TABLE history_rows (
        history_id      INTEGER NOT NULL REFERENCES history(id) ON DELETE CASCADE,
        transaction_id  INTEGER NOT NULL,
        before_json     TEXT,
        after_json      TEXT
    );
    CREATE INDEX history_rows_by_entry ON history_rows (history_id);
    CREATE INDEX history_rows_by_transaction ON history_rows (transaction_id);

    -- Free-form labels on rows, such as a client or a project.
    CREATE TABLE tags (
        id           INTEGER PRIMARY KEY,
        business_id  INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        name         TEXT NOT NULL COLLATE NOCASE,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (business_id, name)
    );
    CREATE TABLE transaction_tags (
        tag_id          INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        PRIMARY KEY (tag_id, transaction_id)
    );
    CREATE INDEX transaction_tags_by_transaction ON transaction_tags (transaction_id);

    -- What a person said about a merchant name: a better name, a contractor flag, or that it is
    -- another spelling of a second payee. A merchant with no entry here is its own payee.
    CREATE TABLE payees (
        id            INTEGER PRIMARY KEY,
        business_id   INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        merchant      TEXT NOT NULL,
        display_name  TEXT,
        contractor    INTEGER NOT NULL DEFAULT 0 CHECK (contractor IN (0, 1)),
        merged_into   INTEGER REFERENCES payees(id) ON DELETE SET NULL,
        UNIQUE (business_id, merchant)
    );

    -- Planned amounts per category, as reports read them: income positive, spending positive.
    -- Month 0 holds a whole year's amount; months 1 to 12 hold one month each.
    CREATE TABLE budgets (
        business_id   INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
        category_id   INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        year          INTEGER NOT NULL,
        month         INTEGER NOT NULL CHECK (month BETWEEN 0 AND 12),
        amount_cents  INTEGER NOT NULL CHECK (typeof(amount_cents) = 'integer' AND amount_cents >= 0),
        PRIMARY KEY (category_id, year, month)
    );
    """,
    _RULE_CONDITIONS_MIGRATION,
]


def legacy_rule_conditions() -> List[str]:
    """The statements of the rule conditions step that turn the rules table's own columns into conditions."""
    return [statement for statement in _statements(_RULE_CONDITIONS_MIGRATION) if "INSERT INTO rule_conditions" in statement]


def resolve_db_path(explicit: Optional[str]) -> Path:
    return Path(explicit or os.environ.get(DB_ENV_VAR) or DEFAULT_DB_NAME)


def friendly_error(path: Path, exc: sqlite3.Error) -> BooksError:
    """Say what a SQLite failure means for the person, and what to do about it."""
    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        return BooksError(f"the books at {path} are busy: another program has them open. Nothing was changed. Close it and try again.")
    if "readonly" in text or "read-only" in text:
        return BooksError(f"the books at {path} are read-only, so nothing was saved. Check the file's permissions.")
    if "full" in text:
        return BooksError(f"the disk holding {path} is full. Nothing was saved. Free some space and try again.")
    if "malformed" in text or "not a database" in text or "disk image" in text or "corrupt" in text:
        return BooksError(
            f"the books at {path} look damaged ({exc}). Keep a copy of the file as it is, then run 'check'. "
            f"{RECOVERY_DOC} walks through what to do next."
        )
    if "unable to open" in text:
        return BooksError(f"cannot open the books at {path}. Check that the folder exists and that you may write to it.")
    return BooksError(f"the books at {path} could not be used: {exc}")


def _check_file(path: Path, create: bool) -> None:
    if path.is_dir():
        raise BooksError(f"{path} is a folder, not a books file.")
    if not path.exists():
        if not create:
            raise BooksError(f"no books at {path}. Run 'init' first, or point --db at your file.")
        return
    try:
        with open(path, "rb") as handle:
            start = handle.read(len(SQLITE_MAGIC))
    except OSError as exc:
        raise BooksError(f"cannot read {path}: {exc.strerror}") from None
    if not start:
        if not create:
            raise BooksError(f"{path} is an empty file, not books. Run 'init' to start books there.")
        return
    if start != SQLITE_MAGIC:
        raise BooksError(
            f"{path} is not a solo-books file. If it used to be your books, it is damaged: see {RECOVERY_DOC}."
        )


def _create_private(path: Path) -> None:
    """New books are readable by their owner only, where the system has such a thing."""
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError as exc:
        raise BooksError(f"cannot create {path}: {exc.strerror}") from None


def connect(path: Path, create: bool = False) -> sqlite3.Connection:
    """Open the books. Without create, a missing file is an error rather than empty books."""
    path = Path(path)
    _check_file(path, create)
    if create:
        _create_private(path)
    try:
        conn = sqlite3.connect(str(path), isolation_level=None, timeout=BUSY_SECONDS)
    except sqlite3.Error as exc:
        raise friendly_error(path, exc) from None
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _migrate(conn, path, create)
    except sqlite3.Error as exc:
        conn.close()
        raise friendly_error(path, exc) from None
    except BaseException:
        conn.close()
        raise
    return conn


def schema_version() -> int:
    return len(MIGRATIONS)


def _refuse_foreign(path: Path) -> None:
    raise BooksError(f"{path} is a database, but it does not hold solo-books. It was left as it is.")


def _migrate(conn: sqlite3.Connection, path: Path, create: bool) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    application_id = conn.execute("PRAGMA application_id").fetchone()[0]
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if version == 0:
        if tables or not create:
            _refuse_foreign(path)
    elif version == 1:
        # The first schema predates the marker in the header, so go by its tables.
        if application_id != 0 or not {"businesses", "accounts", "transactions", "categories"} <= tables:
            _refuse_foreign(path)
    elif application_id != APPLICATION_ID:
        _refuse_foreign(path)
    if version > len(MIGRATIONS):
        raise BooksError(
            f"{path} was written by a newer version of solo-books (schema {version}; this one reads up to "
            f"{len(MIGRATIONS)}). Install the newer version. The file was left as it is."
        )
    if version == len(MIGRATIONS):
        return
    if version > 0:
        _copy_before_upgrade(conn, path, version)
    for number in range(version, len(MIGRATIONS)):
        _apply(conn, number)


def _copy_before_upgrade(conn: sqlite3.Connection, path: Path, version: int) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    copy = path.with_name(f"{path.stem}.schema-{version}-{stamp}{path.suffix}")
    try:
        backup(conn, copy, remember=False)
    except BooksError as exc:
        raise BooksError(f"these books need an upgrade, and the safety copy could not be written: {exc}") from None
    print(
        f"solo-books: upgrading {path} from schema {version} to {len(MIGRATIONS)}. A copy of the old file is at {copy}.",
        file=sys.stderr,
    )


def _statements(script: str) -> List[str]:
    found, buffer = [], ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            found.append(buffer)
            buffer = ""
    return found


def _apply(conn: sqlite3.Connection, number: int) -> None:
    """One schema step, in one transaction. A crash or a second process leaves the file at a whole version."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] == number:
            for statement in _statements(MIGRATIONS[number]):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {number + 1}")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All or nothing. Nested use joins the transaction already open."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        # SQLite rolls back by itself on some failures, such as a full disk.
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value)
    )


def backup(conn: sqlite3.Connection, destination: Path, remember: bool = True) -> None:
    """Write a consistent, checked copy of the books to a new file. The file appears only when it is whole."""
    destination = Path(destination)
    if destination.exists():
        raise BooksError(f"{destination} already exists; backups never overwrite a file.")
    parent = destination.parent if str(destination.parent) else Path(".")
    if not parent.is_dir():
        raise BooksError(f"cannot write {destination}: the folder {parent} does not exist.")
    temporary = None
    try:
        handle, temporary = tempfile.mkstemp(dir=str(parent), prefix=f".{destination.name}.", suffix=".partial")
        os.close(handle)
        target = sqlite3.connect(temporary)
        try:
            conn.backup(target)
            verdict = target.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            target.close()
        if verdict != "ok":
            raise BooksError(f"the copy fails its integrity check ({verdict}), so it was not kept. Run 'check'.")
        os.replace(temporary, destination)
        temporary = None
    except OSError as exc:
        raise BooksError(f"cannot write {destination}: {exc.strerror or exc}") from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    if remember:
        try:
            set_meta(conn, "last_backup_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        except sqlite3.OperationalError:
            # The copy is good. Read-only books just cannot note when it was made.
            pass
