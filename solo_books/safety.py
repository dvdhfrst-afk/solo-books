"""Keeping the books whole: checking them, restoring a backup, and saving what can be read from a damaged file."""
from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

from . import budgets, history, payees, tags
from .db import APPLICATION_ID, BUSY_SECONDS, connect, friendly_error, schema_version
from .csvmap import Mapping
from .errors import BooksError
from .files import looks_like_sqlite
from .ledger import natural_key
from .money import format_cents

_CHUNK = 500
# How far past unreadable rows to keep looking when the table cannot say where it ends.
_PROBE_LIMIT = 20000
_MAX_SHOWN = 10
_LARGEST_PADDED_FILE = 16 * 1024 ** 3


@dataclass
class CheckReport:
    path: Path
    problems: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    version: Optional[int] = None

    @property
    def healthy(self) -> bool:
        return not self.problems


def _open_read_only(path: Path) -> sqlite3.Connection:
    """Open without changing anything, unless an interrupted write has to be rolled back first."""
    conn = None
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=BUSY_SECONDS)
        conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except sqlite3.OperationalError:
        # A network path, or an interrupted write that has to be rolled back first.
        if conn is not None:
            conn.close()
        conn = sqlite3.connect(str(path), timeout=BUSY_SECONDS)
    conn.row_factory = sqlite3.Row
    # Damaged text must not stop the rest of a row from being read.
    conn.text_factory = lambda raw: raw.decode("utf-8", "replace")
    return conn


def check_books(path: Path) -> CheckReport:
    """Look at the file, its structure and the bookkeeping rules. Changes nothing."""
    path = Path(path)
    report = CheckReport(path)
    if not path.is_file():
        raise BooksError(f"no books at {path}.")
    if not looks_like_sqlite(path):
        report.problems.append("The file does not start like a SQLite database, so its first page is damaged or it is another kind of file.")
        return report
    try:
        conn = _open_read_only(path)
    except sqlite3.OperationalError as exc:
        raise friendly_error(path, exc) from None
    except sqlite3.DatabaseError as exc:
        report.problems.append(f"SQLite cannot read the table list: {exc}")
        return report
    try:
        try:
            if not _check_identity(conn, report):
                return report
            _check_structure(conn, report)
            if report.problems:
                # Reading on is still useful: it shows how far the damage reaches.
                report.notes.append("The structure is damaged, so the bookkeeping checks below may be incomplete.")
            _check_bookkeeping(conn, report)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise friendly_error(path, exc) from None
            report.problems.append(f"Reading stopped early: {exc}")
        except sqlite3.DatabaseError as exc:
            report.problems.append(f"Reading stopped early: {exc}")
    finally:
        conn.close()
    return report


def _check_identity(conn: sqlite3.Connection, report: CheckReport) -> bool:
    report.version = conn.execute("PRAGMA user_version").fetchone()[0]
    application_id = conn.execute("PRAGMA application_id").fetchone()[0]
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if report.version > schema_version():
        report.problems.append(f"Written by a newer version of solo-books (schema {report.version}). Install that version.")
        return False
    foreign = (
        report.version == 0
        or (report.version == 1 and not {"businesses", "transactions"} <= tables)
        or (report.version > 1 and application_id != APPLICATION_ID)
    )
    if foreign:
        report.problems.append("This is a database, but it does not hold solo-books.")
        return False
    return True


def _check_structure(conn: sqlite3.Connection, report: CheckReport) -> None:
    lines = [row[0] for row in conn.execute("PRAGMA integrity_check(20)")]
    if lines != ["ok"]:
        report.problems.append(f"SQLite's integrity check found damage: {lines[0]}" + (f" (and {len(lines) - 1} more)" if len(lines) > 1 else ""))
    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        tables = sorted({row[0] for row in broken})
        report.problems.append(f"{len(broken)} row(s) point at something that no longer exists, in: {', '.join(tables)}.")


def _problem(report: CheckReport, conn: sqlite3.Connection, sql: str, message: str) -> None:
    count = conn.execute(sql).fetchone()[0]
    if count:
        report.problems.append(f"{count} {message}")


def _check_bookkeeping(conn: sqlite3.Connection, report: CheckReport) -> None:
    for name, sql in (
        ("businesses", "SELECT COUNT(*) FROM businesses"),
        ("accounts", "SELECT COUNT(*) FROM accounts"),
        ("rows", "SELECT COUNT(*) FROM transactions"),
        ("waiting", "SELECT COUNT(*) FROM transactions WHERE category_id IS NULL"),
        ("rules", "SELECT COUNT(*) FROM rules"),
        ("finished", "SELECT COUNT(*) FROM reconciliations WHERE status = 'finished'"),
    ):
        report.counts[name] = conn.execute(sql).fetchone()[0]
    _problem(
        report, conn,
        "SELECT COUNT(*) FROM transactions t JOIN accounts a ON a.id = t.account_id"
        " JOIN categories c ON c.id = t.category_id WHERE c.business_id != a.business_id",
        "row(s) are booked to another business's category.",
    )
    _problem(
        report, conn,
        "SELECT COUNT(*) FROM rules r JOIN categories c ON c.id = r.category_id WHERE c.business_id != r.business_id",
        "rule(s) book to another business's category.",
    )
    _problem(
        report, conn,
        "SELECT COUNT(*) FROM transactions WHERE (category_id IS NULL) != (category_source IS NULL)",
        "row(s) disagree with themselves about whether they are booked.",
    )
    _problem(
        report, conn,
        "SELECT COUNT(*) FROM transactions t JOIN reconciliations r ON r.id = t.reconciliation_id"
        " WHERE r.status != 'finished' OR r.account_id != t.account_id"
        " OR t.posted_on < r.period_start OR t.posted_on > r.period_end",
        "row(s) are locked to a reconciliation that does not cover them.",
    )
    _problem(
        report, conn,
        "SELECT COUNT(*) FROM transactions t JOIN imports i ON i.id = t.import_id WHERE i.undone_at IS NOT NULL",
        "row(s) still belong to an import that was undone.",
    )
    _check_rows(conn, report)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    # Books from before a table existed are read as they are, since 'check' never upgrades a file.
    for table, module in (("history", history), ("tags", tags), ("payees", payees), ("budgets", budgets)):
        if table in tables:
            report.problems.extend(module.problems(conn))
    if "history" in tables:
        report.counts["history"] = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    for account in conn.execute("SELECT name, mapping_json FROM accounts WHERE mapping_json IS NOT NULL").fetchall():
        try:
            Mapping.from_json(account["mapping_json"])
        except BooksError as exc:
            report.problems.append(f"Account {account['name']}: its column mapping is unusable ({exc}). Set it again with 'account map'.")
    for recon in conn.execute(
        "SELECT r.id, r.statement_begin_cents AS begin_cents, r.statement_end_cents AS end_cents, r.account_id,"
        " r.period_start, r.period_end, a.name FROM reconciliations r JOIN accounts a ON a.id = r.account_id"
        " WHERE r.status = 'finished'"
    ).fetchall():
        activity = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM transactions WHERE account_id = ? AND posted_on BETWEEN ? AND ?",
            (recon["account_id"], recon["period_start"], recon["period_end"]),
        ).fetchone()[0]
        off = recon["end_cents"] - recon["begin_cents"] - activity
        if off:
            report.notes.append(
                f"Finished reconciliation {recon['id']} ({recon['name']}, {recon['period_start']} to {recon['period_end']}) "
                f"is off by {format_cents(off)}. See it with 'reconcile show {recon['id']}'."
            )


def _check_rows(conn: sqlite3.Connection, report: CheckReport) -> None:
    bad_dates = mismatched = 0
    for row in conn.execute("SELECT posted_on, amount_cents, description, natural_key FROM transactions"):
        try:
            day = date.fromisoformat(row["posted_on"])
        except (TypeError, ValueError):
            bad_dates += 1
            continue
        if natural_key(day, row["amount_cents"], row["description"]) != row["natural_key"]:
            mismatched += 1
    if bad_dates:
        report.problems.append(f"{bad_dates} row(s) have a date that is not a date.")
    if mismatched:
        report.problems.append(
            f"{mismatched} row(s) changed after they were written, by something other than this program. "
            "Re-importing could double them."
        )


# Restore

def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _sidecars(path: Path) -> List[Path]:
    return [path.with_name(path.name + suffix) for suffix in ("-journal", "-wal", "-shm")]


def _require_idle(path: Path) -> None:
    """Refuse while another program is using the books. Damaged books may not answer, and that is fine."""
    if not path.exists() or not looks_like_sqlite(path):
        return
    conn = sqlite3.connect(str(path), timeout=1, isolation_level=None)
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise friendly_error(path, exc) from None
    except sqlite3.DatabaseError:
        pass
    finally:
        conn.close()


def restore(books_path: Path, backup_path: Path) -> Optional[Path]:
    """Put a backup in place of the books. The books as they were are kept beside it, under a new name."""
    books_path, backup_path = Path(books_path), Path(backup_path)
    if not backup_path.is_file():
        raise BooksError(f"no backup at {backup_path}.")
    if backup_path.resolve() == books_path.resolve():
        raise BooksError(f"{backup_path} is the books file itself.")
    verdict = check_books(backup_path)
    if not verdict.healthy:
        raise BooksError(
            f"{backup_path} is not a good backup, so nothing was changed:\n  " + "\n  ".join(verdict.problems)
        )
    if books_path.is_dir():
        raise BooksError(f"{books_path} is a folder, not a books file.")
    _require_idle(books_path)
    incoming = books_path.with_name(f".{books_path.name}.{_stamp()}.restoring")
    kept: Optional[Path] = None
    moved: List[Tuple[Path, Path]] = []
    try:
        books_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(backup_path), str(incoming))
        with open(incoming, "rb+") as handle:
            os.fsync(handle.fileno())
        if books_path.exists():
            kept = books_path.with_name(f"{books_path.stem}.before-restore-{_stamp()}{books_path.suffix}")
            # A leftover journal belongs to the old file. Beside the new one it would corrupt it.
            for source in [books_path, *[side for side in _sidecars(books_path) if side.exists()]]:
                target = source.with_name(kept.name + source.name[len(books_path.name):])
                os.replace(source, target)
                moved.append((source, target))
        os.replace(incoming, books_path)
    except OSError as exc:
        for source, target in reversed(moved):
            os.replace(target, source)
        if incoming.exists():
            incoming.unlink()
        raise BooksError(f"cannot restore over {books_path}: {exc.strerror or exc}. Nothing was changed.") from None
    return kept


# Salvage

@dataclass
class SalvageReport:
    copied: Dict[str, int] = field(default_factory=dict)
    unreadable: Dict[str, int] = field(default_factory=dict)
    unknown_loss: Set[str] = field(default_factory=set)
    refused: Dict[str, int] = field(default_factory=dict)
    detached: int = 0
    removed: int = 0
    cut_short: bool = False

    @property
    def lost_anything(self) -> bool:
        return bool(self.unreadable or self.unknown_loss or self.refused or self.removed or self.cut_short)


def _readable_rows(source: sqlite3.Connection, table: str, report: SalvageReport) -> Iterator[sqlite3.Row]:
    """Every row that can be read, walking past the ones that cannot."""
    quoted = f'"{table}"'
    try:
        top = source.execute(f"SELECT MAX(rowid) FROM {quoted}").fetchone()[0]
    except sqlite3.DatabaseError:
        top = None
    last = 0
    while True:
        try:
            batch = source.execute(f"SELECT rowid AS rowid_, * FROM {quoted} WHERE rowid > ? ORDER BY rowid LIMIT ?", (last, _CHUNK)).fetchall()
        except sqlite3.DatabaseError:
            batch = None
        if batch == []:
            return
        if batch is not None:
            yield from batch
            last = batch[-1]["rowid_"]
            continue
        # Something in this stretch is unreadable. Ask for one row at a time until reads work again.
        probe, misses = last + 1, 0
        recovered = False
        while misses < _PROBE_LIMIT and (top is None or probe <= top):
            try:
                row = source.execute(f"SELECT rowid AS rowid_, * FROM {quoted} WHERE rowid = ?", (probe,)).fetchone()
            except sqlite3.DatabaseError:
                # Without a known last row, these misses say nothing about how many rows there were.
                if top is None:
                    report.unknown_loss.add(table)
                else:
                    report.unreadable[table] = report.unreadable.get(table, 0) + 1
                row = None
            if row is not None:
                yield row
                last = probe
                recovered = True
                break
            probe, misses = probe + 1, misses + 1
        if not recovered:
            return


def _clear_broken_links(target: sqlite3.Connection, report: SalvageReport) -> None:
    """A row whose parent was lost cannot stay as it is. Optional links are cleared; a row that needs its parent goes."""
    for _ in range(6):
        broken = target.execute("PRAGMA foreign_key_check").fetchall()
        if not broken:
            break
        for table, rowid, _parent, fk_id in broken:
            column = None
            for link in target.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
                if link[0] == fk_id:
                    column = link[3]
            required = any(
                info[1] == column and info[3] for info in target.execute(f'PRAGMA table_info("{table}")').fetchall()
            )
            if required or rowid is None:
                target.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (rowid,))
                report.removed += 1
            else:
                target.execute(f'UPDATE "{table}" SET "{column}" = NULL WHERE rowid = ?', (rowid,))
                report.detached += 1
    target.execute("UPDATE transactions SET category_source = NULL, rule_id = NULL WHERE category_id IS NULL")


class _NoTableList(BooksError):
    pass


def _padded_copy(damaged: Path, destination: Path) -> Optional[Path]:
    """A file cut short, as by an interrupted copy, reads as damaged throughout. Restored to full length, most of it reads."""
    try:
        with open(damaged, "rb") as handle:
            header = handle.read(32)
        page_size = int.from_bytes(header[16:18], "big")
        whole = (65536 if page_size == 1 else page_size) * int.from_bytes(header[28:32], "big")
        if not damaged.stat().st_size < whole <= _LARGEST_PADDED_FILE:
            return None
        padded = destination.with_name(destination.name + ".padded")
        shutil.copyfile(str(damaged), str(padded))
        with open(padded, "r+b") as handle:
            handle.truncate(whole)
        return padded
    except OSError:
        return None


def salvage(damaged: Path, destination: Path) -> SalvageReport:
    """Copy every readable row into new books. The damaged file is only read."""
    damaged, destination = Path(damaged), Path(destination)
    if destination.exists():
        raise BooksError(f"{destination} already exists. Choose a new file name.")
    if not damaged.is_file():
        raise BooksError(f"no books at {damaged}.")
    if not looks_like_sqlite(damaged):
        raise BooksError(f"{damaged} no longer starts like a database, so nothing can be read from it. Restore a backup instead.")
    try:
        return _copy_readable(damaged, destination, damaged)
    except _NoTableList as first_failure:
        padded = _padded_copy(damaged, destination)
        if padded is None:
            raise BooksError(str(first_failure)) from None
    try:
        report = _copy_readable(padded, destination, damaged)
    except _NoTableList as second_failure:
        raise BooksError(str(second_failure)) from None
    finally:
        padded.unlink()
    report.cut_short = True
    return report


def _copy_readable(source_path: Path, destination: Path, damaged: Path) -> SalvageReport:
    report = SalvageReport()
    try:
        source = _open_read_only(source_path)
        names = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError as exc:
        raise _NoTableList(f"the table list in {damaged} cannot be read ({exc}). Restore a backup instead.") from None
    if not {"businesses", "transactions"} <= names:
        source.close()
        raise BooksError(f"{damaged} does not hold solo-books tables that can be read. Restore a backup instead.")
    target = connect(destination, create=True)
    target.execute("PRAGMA foreign_keys = OFF")
    try:
        target.execute("BEGIN")
        tables = [
            row[0]
            for row in target.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
        ]
        for table in tables:
            if table not in names:
                continue
            columns = [row[1] for row in target.execute(f'PRAGMA table_info("{table}")').fetchall()]
            try:
                available = {row[1] for row in source.execute(f'PRAGMA table_info("{table}")').fetchall()}
            except sqlite3.DatabaseError:
                report.unreadable[table] = report.unreadable.get(table, 0) + 1
                continue
            shared = [column for column in columns if column in available]
            sql = f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in shared)}) VALUES ({", ".join("?" * len(shared))})'
            for row in _readable_rows(source, table, report):
                try:
                    target.execute(sql, [row[column] for column in shared])
                    report.copied[table] = report.copied.get(table, 0) + 1
                except sqlite3.IntegrityError:
                    report.refused[table] = report.refused.get(table, 0) + 1
        _clear_broken_links(target, report)
        target.execute("COMMIT")
    except BaseException:
        if target.in_transaction:
            target.execute("ROLLBACK")
        target.close()
        source.close()
        if destination.exists():
            destination.unlink()
        raise
    target.close()
    source.close()
    return report
