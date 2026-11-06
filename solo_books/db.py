"""The SQLite file: opening it, its schema, and moving it forward between versions."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .errors import BooksError

DEFAULT_DB_NAME = "books.sqlite3"
DB_ENV_VAR = "SOLO_BOOKS_DB"

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
]


def resolve_db_path(explicit: Optional[str]) -> Path:
    return Path(explicit or os.environ.get(DB_ENV_VAR) or DEFAULT_DB_NAME)


def connect(path: Path, create: bool = False) -> sqlite3.Connection:
    """Open the books. Without create, a missing file is an error rather than empty books."""
    path = Path(path)
    if not create and not path.exists():
        raise BooksError(f"no books at {path}. Run 'init' first, or point --db at your file.")
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn, path)
    return conn


def _migrate(conn: sqlite3.Connection, path: Path) -> None:
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise BooksError(f"{path} is not a solo-books file: {exc}") from None
    if version > len(MIGRATIONS):
        raise BooksError(f"{path} was written by a newer version of solo-books.")
    for number in range(version, len(MIGRATIONS)):
        conn.executescript(
            f"BEGIN;\n{MIGRATIONS[number]}\nPRAGMA user_version = {number + 1};\nCOMMIT;"
        )


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
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def backup(conn: sqlite3.Connection, destination: Path) -> None:
    """Write a consistent copy of the books to a new file."""
    destination = Path(destination)
    if destination.exists():
        raise BooksError(f"{destination} already exists; backups never overwrite a file.")
    target = sqlite3.connect(str(destination))
    try:
        conn.backup(target)
    finally:
        target.close()
