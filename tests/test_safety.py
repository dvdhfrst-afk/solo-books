"""Backup, restore, check, salvage and schema upgrades: the books stay whole, and old data stays."""
from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import unittest
from pathlib import Path

from solo_books import db, safety
from solo_books.errors import BooksError
from tests.helpers import REPO_ROOT, WORKED, BooksCase, parse_csv, worked_expected

V1_SQL = REPO_ROOT / "tests" / "fixtures" / "books-schema-v1.sql"


def make_v1(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(V1_SQL.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()


class Filled(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "Chk", "--kind", "checking", "--opening-balance", "100.00", "--opening-date", "2025-01-01", "--date-format", "%Y-%m-%d")
        lines = ["Date,Description,Amount"]
        lines.extend(f"2025-01-{(i % 28) + 1:02d},MERCHANT {i},-{i + 1}.00" for i in range(3000))
        self.ok("import", "Chk", str(self.write("rows.csv", "\n".join(lines) + "\n")))
        self.ok("rules", "add", "--contains", "MERCHANT 1", "--category", "Meals")
        self.conn.close()

    def rows(self, path: Path) -> int:
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        finally:
            conn.close()


class Backup(Filled):
    def test_a_backup_is_a_full_healthy_copy(self) -> None:
        target = self.dir / "b.sqlite3"
        self.assertIn("3000 row(s)", self.ok("backup", str(target)))
        self.assertEqual(self.rows(target), 3000)
        self.assertTrue(safety.check_books(target).healthy)

    def test_with_no_name_it_goes_beside_the_books_with_a_date(self) -> None:
        self.ok("backup")
        made = [path for path in self.dir.iterdir() if path.name.startswith("books-backup-")]
        self.assertEqual(len(made), 1)

    def test_status_remembers_the_last_backup(self) -> None:
        self.assertIn("never", self.ok("status"))
        self.ok("backup", str(self.dir / "b.sqlite3"))
        self.assertIn("UTC", self.ok("status"))

    def test_it_never_overwrites_and_leaves_no_scraps(self) -> None:
        target = self.dir / "b.sqlite3"
        target.write_bytes(b"precious")
        self.assertIn("never overwrite", self.fails("backup", str(target)))
        self.assertEqual(target.read_bytes(), b"precious")
        self.assertEqual([path for path in self.dir.iterdir() if path.name.endswith(".partial")], [])

    @unittest.skipIf(os.name == "nt", "owner-only file modes exist only on POSIX")
    def test_new_books_are_private(self) -> None:
        path = self.dir / "fresh.sqlite3"
        db.connect(path, create=True).close()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class Restore(Filled):
    def test_restore_brings_back_the_backup_and_keeps_what_was_there(self) -> None:
        backup = self.dir / "b.sqlite3"
        self.ok("backup", str(backup))
        self.ok("account", "delete", "Chk", "--with-rows")
        self.assertEqual(self.rows(self.db_path), 0)
        out = self.ok("restore", str(backup))
        self.assertIn("kept at", out)
        self.assertEqual(self.rows(self.db_path), 3000)
        kept = [path for path in self.dir.iterdir() if ".before-restore-" in path.name]
        self.assertEqual(len(kept), 1)
        self.assertEqual(self.rows(kept[0]), 0)
        self.assertIn("no problems found", self.ok("check"))

    def test_a_stale_journal_cannot_touch_the_restored_books(self) -> None:
        backup = self.dir / "b.sqlite3"
        self.ok("backup", str(backup))
        journal = Path(str(self.db_path) + "-journal")
        journal.write_bytes(b"leftover from a crash")
        self.ok("restore", str(backup))
        self.assertFalse(journal.exists())
        self.assertIn("no problems found", self.ok("check"))

    def test_a_bad_backup_changes_nothing(self) -> None:
        for name, data in (("junk.sqlite3", os.urandom(4096)), ("empty.sqlite3", b"")):
            path = self.dir / name
            path.write_bytes(data)
            code, _, err = self.run_cli("restore", str(path))
            self.assertEqual(code, 2, name)
            self.assertIn("not a good backup", err)
        self.assertEqual(self.rows(self.db_path), 3000)

    def test_another_programs_database_is_refused(self) -> None:
        other = self.dir / "other.sqlite3"
        conn = sqlite3.connect(str(other))
        conn.execute("CREATE TABLE photos (id INTEGER)")
        conn.commit()
        conn.close()
        self.assertIn("does not hold solo-books", self.fails("restore", str(other)))
        self.assertEqual(self.rows(self.db_path), 3000)

    def test_books_in_use_are_not_replaced(self) -> None:
        backup = self.dir / "b.sqlite3"
        self.ok("backup", str(backup))
        blocker = sqlite3.connect(str(self.db_path), isolation_level=None)
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            self.assertIn("busy", self.fails("restore", str(backup)))
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

    def test_restore_works_when_the_books_are_gone(self) -> None:
        backup = self.dir / "b.sqlite3"
        self.ok("backup", str(backup))
        self.db_path.unlink()
        self.assertNotIn("kept at", self.ok("restore", str(backup)))
        self.assertEqual(self.rows(self.db_path), 3000)

    def test_a_backup_cannot_be_restored_over_itself(self) -> None:
        self.assertIn("books file itself", self.fails("restore", str(self.db_path)))


def smash_table_page(path: Path, table: str) -> None:
    """Overwrite a page in the middle of one table, the way a failing disk would."""
    conn = sqlite3.connect(str(path))
    size = conn.execute("PRAGMA page_size").fetchone()[0]
    try:
        offsets = [row[0] for row in conn.execute("SELECT pageno FROM dbstat WHERE name = ? AND pagetype = 'leaf'", (table,))]
    except sqlite3.OperationalError:
        offsets = []
    conn.close()
    data = bytearray(path.read_bytes())
    pages = len(data) // size
    page = offsets[len(offsets) // 2] if offsets else pages // 2
    data[(page - 1) * size:page * size] = b"\xa5" * size
    path.write_bytes(bytes(data))


class Check(Filled):
    def assert_finds(self, text: str) -> None:
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1, out)
        self.assertIn(text, out)
        self.assertIn("RECOVERY", out)

    def edit(self, sql: str) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(sql)
        conn.commit()
        conn.close()

    def test_healthy_books_pass_and_report_their_size(self) -> None:
        out = self.ok("check")
        self.assertIn("3000 row(s)", out)
        self.assertIn("no problems found", out)

    def test_check_changes_nothing(self) -> None:
        before = self.db_path.read_bytes()
        self.ok("check")
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_it_sees_damage_on_disk(self) -> None:
        smash_table_page(self.db_path, "transactions")
        self.assert_finds("PROBLEM")

    def test_it_sees_a_file_overwritten_from_the_start(self) -> None:
        data = bytearray(self.db_path.read_bytes())
        data[:100] = b"\x00" * 100
        self.db_path.write_bytes(bytes(data))
        self.assert_finds("does not start like a SQLite database")

    def test_it_sees_a_truncated_file(self) -> None:
        data = self.db_path.read_bytes()
        self.db_path.write_bytes(data[: len(data) // 2])
        self.assert_finds("PROBLEM")

    def test_it_sees_rows_changed_behind_its_back(self) -> None:
        self.edit("UPDATE transactions SET amount_cents = 1 WHERE id = 5")
        self.assert_finds("changed after they were written")

    def test_it_sees_a_date_that_is_not_a_date(self) -> None:
        self.edit("UPDATE transactions SET posted_on = 'soon' WHERE id = 5")
        self.assert_finds("not a date")

    def test_it_sees_a_category_from_another_business(self) -> None:
        self.ok("business", "add", "Business B")
        self.edit(
            "UPDATE transactions SET category_source = 'manual', category_id ="
            " (SELECT c.id FROM categories c JOIN businesses b ON b.id = c.business_id WHERE b.name = 'Business B' LIMIT 1)"
            " WHERE id = 5"
        )
        self.assert_finds("another business")

    def test_it_sees_a_half_booked_row(self) -> None:
        self.edit("UPDATE transactions SET category_source = 'manual' WHERE category_id IS NULL AND id = 5")
        self.assert_finds("disagree")

    def test_it_sees_a_broken_link(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE transactions SET rule_id = 999 WHERE id = 5")
        conn.commit()
        conn.close()
        self.assert_finds("no longer exists")

    def test_it_sees_an_unusable_mapping(self) -> None:
        self.edit("UPDATE accounts SET mapping_json = '{broken'")
        self.assert_finds("mapping is unusable")

    def test_a_late_row_in_a_finished_period_is_a_note_not_damage(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        total = conn.execute("SELECT SUM(amount_cents) FROM transactions").fetchone()[0]
        conn.close()
        self.ok("reconcile", "run", "Chk", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "100.00", "--end", f"{(10000 + total) / 100:.2f}")
        self.ok("reconcile", "finish", "1")
        self.ok("txn", "add", "Chk", "--date", "2025-01-15", "--amount", "-1.00", "--description", "LATE")
        out = self.ok("check")
        self.assertIn("is off by", out)
        self.assertIn("no problems found", out)

    def test_it_refuses_other_files_without_touching_them(self) -> None:
        other = self.dir / "other.sqlite3"
        conn = sqlite3.connect(str(other))
        conn.execute("CREATE TABLE photos (id INTEGER)")
        conn.commit()
        conn.close()
        before = other.read_bytes()
        code, out, _ = self.run_cli("--db", str(other), "check")
        self.assertEqual(code, 1)
        self.assertIn("does not hold solo-books", out)
        self.assertEqual(other.read_bytes(), before)
        self.assertEqual(self.run_cli("--db", str(self.dir / "missing.sqlite3"), "check")[0], 2)


class Salvage(Filled):
    def test_it_keeps_what_can_be_read_and_never_touches_the_damaged_file(self) -> None:
        smash_table_page(self.db_path, "transactions")
        damaged = self.db_path.read_bytes()
        target = self.dir / "saved.sqlite3"
        code, out, _ = self.run_cli("salvage", str(target))
        self.assertIn(code, (0, 1), out)
        self.assertEqual(self.db_path.read_bytes(), damaged)
        saved = self.rows(target)
        self.assertGreater(saved, 2000)
        self.assertLess(saved, 3000)
        self.assertTrue(safety.check_books(target).healthy, safety.check_books(target).problems)

    def test_importing_again_fills_the_gap_without_doubling(self) -> None:
        smash_table_page(self.db_path, "transactions")
        target = self.dir / "saved.sqlite3"
        self.run_cli("salvage", str(target))
        code, out, err = self.run_cli("--db", str(target), "import", "Chk", str(self.dir / "rows.csv"))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.rows(target), 3000)
        self.assertEqual(self.run_cli("--db", str(target), "check")[0], 0)

    def test_healthy_books_come_through_whole(self) -> None:
        target = self.dir / "saved.sqlite3"
        code, out, _ = self.run_cli("salvage", str(target))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.rows(target), 3000)
        self.assertNotIn("could not be read", out)

    def test_a_row_whose_parent_was_lost_loses_only_the_link(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("UPDATE transactions SET category_id = 999, category_source = 'manual', rule_id = 998 WHERE id = 5")
        conn.commit()
        conn.close()
        target = self.dir / "saved.sqlite3"
        code, out, _ = self.run_cli("salvage", str(target))
        self.assertEqual(code, 0, out)
        conn = sqlite3.connect(str(target))
        row = conn.execute("SELECT category_id, category_source, rule_id FROM transactions WHERE id = 5").fetchone()
        conn.close()
        self.assertEqual(row, (None, None, None))

    def test_it_never_claims_more_lost_rows_than_the_file_could_hold(self) -> None:
        smash_table_page(self.db_path, "transactions")
        out = self.run_cli("salvage", str(self.dir / "saved.sqlite3"))[1]
        for line in out.splitlines():
            if "could not be read" in line and "up to" in line:
                self.assertLessEqual(int(line.split("up to ")[1].split()[0]), 3000, line)

    def test_a_file_cut_short_still_gives_up_what_it_holds(self) -> None:
        data = self.db_path.read_bytes()
        for keep in (len(data) - 4096, len(data) // 2):
            self.db_path.write_bytes(data[:keep])
            target = self.dir / f"saved-{keep}.sqlite3"
            code, out, err = self.run_cli("salvage", str(target))
            self.assertIn(code, (0, 1), err)
            self.assertGreater(self.rows(target), 0)
            self.assertEqual([p for p in self.dir.iterdir() if p.name.endswith(".padded")], [])
            self.assertEqual(self.db_path.read_bytes(), data[:keep])
            self.assertTrue(safety.check_books(target).healthy)

    def test_a_missing_file_is_called_missing(self) -> None:
        self.db_path.unlink()
        self.assertIn("no books at", self.fails("salvage", str(self.dir / "saved.sqlite3")))

    def test_it_never_overwrites(self) -> None:
        target = self.dir / "saved.sqlite3"
        target.write_bytes(b"precious")
        self.assertIn("already exists", self.fails("salvage", str(target)))
        self.assertEqual(target.read_bytes(), b"precious")

    def test_a_file_with_no_table_list_says_to_restore(self) -> None:
        data = bytearray(self.db_path.read_bytes())
        data[100:4096] = b"\xa5" * 3996
        self.db_path.write_bytes(bytes(data))
        code, _, err = self.run_cli("salvage", str(self.dir / "saved.sqlite3"))
        self.assertEqual(code, 2)
        self.assertIn("Restore a backup", err)
        self.assertFalse((self.dir / "saved.sqlite3").exists())


class ForeignFiles(BooksCase):
    def test_another_programs_database_gets_no_tables(self) -> None:
        other = self.dir / "other.sqlite3"
        conn = sqlite3.connect(str(other))
        conn.execute("CREATE TABLE photos (id INTEGER)")
        conn.commit()
        conn.close()
        for command in (("status",), ("init",), ("business", "add", "X")):
            code, _, err = self.run_cli("--db", str(other), *command)
            self.assertEqual(code, 2, command)
            self.assertIn("does not hold solo-books", err)
        conn = sqlite3.connect(str(other))
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        conn.close()
        self.assertEqual(names, ["photos"])

    def test_a_marked_file_of_another_kind_is_refused_at_any_version(self) -> None:
        for version in (1, 2, 50):
            other = self.dir / f"other{version}.sqlite3"
            conn = sqlite3.connect(str(other))
            conn.execute("CREATE TABLE photos (id INTEGER)")
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
            conn.close()
            self.assertIn("does not hold solo-books", self.fails("--db", str(other), "status"))

    def test_junk_a_folder_and_an_empty_file(self) -> None:
        junk = self.dir / "junk.sqlite3"
        junk.write_bytes(os.urandom(4096))
        self.assertIn("not a solo-books file", self.fails("--db", str(junk), "status"))
        self.assertIn("folder", self.fails("--db", str(self.dir), "status"))
        self.assertIn("folder", self.fails("--db", str(self.dir), "init"))
        empty = self.dir / "empty.sqlite3"
        empty.write_bytes(b"")
        self.assertIn("empty file", self.fails("--db", str(empty), "status"))
        self.assertIn("Created", self.ok("--db", str(empty), "init"))

    def test_damage_reads_as_damage_not_a_stack_trace(self) -> None:
        self.ok("business", "add", "Business A")
        self.conn.close()
        smash_table_page(self.db_path, "businesses")
        code, out, err = self.run_cli("business", "list")
        self.assertEqual(code, 2, out)
        self.assertIn("damaged", err)
        self.assertNotIn("Traceback", err)

    def test_a_newer_file_is_left_alone(self) -> None:
        self.conn.close()
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA user_version = 99")
        conn.commit()
        conn.close()
        before = self.db_path.read_bytes()
        self.assertIn("newer version", self.fails("status"))
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_init_on_existing_books_changes_nothing(self) -> None:
        self.ok("business", "add", "Business A")
        self.assertIn("already exist", self.ok("init"))
        self.assertIn("Business A", self.ok("business", "list"))


class Upgrades(BooksCase):
    """A real file written by the first schema, frozen as SQL before the schema moved on."""

    def setUp(self) -> None:
        super().setUp()
        self.conn.close()
        self.db_path.unlink()
        make_v1(self.db_path)

    def test_the_fixture_is_really_the_old_schema(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA application_id").fetchone()[0], 0)
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'meta'").fetchone())
        conn.close()

    def test_every_row_survives_and_the_figures_still_tie(self) -> None:
        old = sqlite3.connect(str(self.db_path))
        tables = ("businesses", "categories", "accounts", "imports", "rules", "transactions", "inventory_counts", "import_rows")
        # Later schemas add columns. Every column the first schema had must still hold what it held.
        columns = {table: ", ".join(row[1] for row in old.execute(f"PRAGMA table_info({table})")) for table in tables}
        before = {table: old.execute(f"SELECT {columns[table]} FROM {table} ORDER BY 1, 2").fetchall() for table in tables}
        old.close()
        code, out, err = self.run_cli("account", "show", "A Checking")
        self.assertEqual(code, 0, err)
        self.assertIn("upgrading", err)
        conn = sqlite3.connect(str(self.db_path))
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.schema_version())
        self.assertEqual(conn.execute("PRAGMA application_id").fetchone()[0], db.APPLICATION_ID)
        for table, rows in before.items():
            self.assertEqual(conn.execute(f"SELECT {columns[table]} FROM {table} ORDER BY 1, 2").fetchall(), rows, table)
        conn.close()
        self.assertIn("11,928.15", out)
        self.assertIn("no problems found", self.ok("check"))

    def test_the_old_file_is_kept_beside_the_new_one_once(self) -> None:
        self.ok("status")
        self.ok("status")
        kept = [path for path in self.dir.iterdir() if ".schema-1-" in path.name]
        self.assertEqual(len(kept), 1)
        conn = sqlite3.connect(str(kept[0]))
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 20)
        conn.close()

    def test_reports_match_the_hand_figures_after_upgrade(self) -> None:
        expected = worked_expected()["business_a"]["pnl"]
        rows = parse_csv(self.ok("report", "pnl", "--period", "2025-Q1", "--business", "Business A", "--format", "csv"))
        income = {row["code"]: row["amount"] for row in rows if row["code"]}
        self.assertEqual(income["4000"], expected["4000"])

    def test_importing_the_same_files_again_adds_nothing(self) -> None:
        # The old import records name fewer mapping fields than today's. That must not read as a new mapping.
        for account, name in (
            ("A Checking", "a_checking_jan_feb.csv"), ("A Checking", "a_checking_feb_mar.csv"),
            ("A Card", "a_card_q1.csv"), ("B Checking", "b_checking_q1.csv"),
        ):
            out = self.ok("import", account, str(WORKED / name))
            self.assertIn("new:                0", out)

    def test_a_failed_step_leaves_the_old_version_whole(self) -> None:
        original = list(db.MIGRATIONS)
        db.MIGRATIONS.append("CREATE TABLE half_done (id INTEGER);\nCREATE TABLE half_done (id INTEGER);\n")
        self.addCleanup(lambda: db.MIGRATIONS.__setitem__(slice(None), original))
        code, _, err = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)
        conn = sqlite3.connect(str(self.db_path))
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], len(original))
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'half_done'").fetchone())
        conn.close()

    def test_a_read_only_old_file_says_it_needs_upgrading(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root can write to anything")
        folder = self.dir / "ro"
        folder.mkdir()
        target = folder / "books.sqlite3"
        shutil.copy(str(self.db_path), str(target))
        os.chmod(target, stat.S_IREAD)
        self.addCleanup(os.chmod, target, stat.S_IREAD | stat.S_IWRITE)
        code, _, err = self.run_cli("--db", str(target), "status")
        self.assertEqual(code, 2)
        self.assertIn("read-only", err)
        conn = sqlite3.connect(str(target))
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        conn.close()


class Housekeeping(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "Chk", "--kind", "checking", "--opening-balance", "10", "--opening-date", "2025-01-01")

    def test_rename_business_and_account(self) -> None:
        self.ok("business", "rename", "Business A", "Business Z")
        self.ok("account", "rename", "Chk", "Main")
        self.assertIn("Main", self.ok("account", "list"))
        self.assertIn("Business Z", self.ok("business", "list"))
        self.ok("business", "add", "Business Q")
        self.assertIn("already a business", self.fails("business", "rename", "Business Q", "business z"))
        self.ok("account", "add", "Other", "--kind", "savings", "--business", "Business Q")
        self.assertIn("already an account", self.fails("account", "rename", "Other", "main"))

    def test_delete_account_needs_asking_when_it_holds_rows(self) -> None:
        self.ok("txn", "add", "Chk", "--date", "2025-01-02", "--amount", "-1.00", "--description", "CAFE")
        self.assertIn("--with-rows", self.fails("account", "delete", "Chk"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
        self.ok("account", "delete", "Chk", "--with-rows")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)

    def test_delete_account_with_a_finished_period_is_refused(self) -> None:
        self.ok("txn", "add", "Chk", "--date", "2025-01-02", "--amount", "-1.00", "--description", "CAFE")
        self.ok("reconcile", "run", "Chk", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "10.00", "--end", "9.00")
        self.ok("reconcile", "finish", "1")
        self.assertIn("finished", self.fails("account", "delete", "Chk", "--with-rows"))

    def test_delete_business_only_when_it_has_no_accounts(self) -> None:
        self.ok("rules", "add", "--contains", "X", "--category", "Meals")
        self.assertIn("still has accounts", self.fails("business", "delete", "Business A"))
        self.ok("account", "delete", "Chk")
        self.ok("business", "delete", "Business A")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0], 0)

    def test_edit_a_row_added_by_hand(self) -> None:
        self.ok("txn", "add", "Chk", "--date", "2025-01-02", "--amount", "-1.00", "--description", "CAEF")
        self.ok("txn", "edit", "1", "--description", "CAFE", "--amount", "-2,000.50", "--date", "2025-01-03")
        row = self.conn.execute("SELECT * FROM transactions").fetchone()
        self.assertEqual((row["description"], row["amount_cents"], row["posted_on"], row["merchant"]), ("CAFE", -200050, "2025-01-03", "CAFE"))
        self.assertIn("no problems found", self.ok("check"))

    def test_an_edited_row_is_recognized_when_the_export_arrives(self) -> None:
        self.ok("txn", "add", "Chk", "--date", "2025-01-02", "--amount", "-1.00", "--description", "CAEF")
        self.ok("txn", "edit", "1", "--description", "CAFE")
        self.ok("account", "map", "Chk", "--date-format", "%Y-%m-%d")
        path = self.write("x.csv", "Date,Description,Amount\n2025-01-02,CAFE,-1.00\n")
        self.assertIn("new:                0", self.ok("import", "Chk", str(path)))

    def test_rows_from_a_bank_file_stay_as_written(self) -> None:
        self.ok("account", "map", "Chk", "--date-format", "%Y-%m-%d")
        path = self.write("x.csv", "Date,Description,Amount\n2025-01-02,CAFE,-1.00\n")
        self.ok("import", "Chk", str(path))
        self.assertIn("bank file", self.fails("txn", "edit", "1", "--amount", "-9.00"))

    def test_edit_needs_something_to_change_and_respects_a_finished_period(self) -> None:
        self.ok("txn", "add", "Chk", "--date", "2025-01-02", "--amount", "-1.00", "--description", "CAFE")
        self.assertIn("nothing to change", self.fails("txn", "edit", "1"))
        self.assertIn("zero", self.fails("txn", "edit", "1", "--amount", "0"))
        self.ok("reconcile", "run", "Chk", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "10.00", "--end", "9.00")
        self.ok("reconcile", "finish", "1")
        self.assertIn("Reopen", self.fails("txn", "edit", "1", "--amount", "-2.00"))

    def test_status_says_what_to_do_first(self) -> None:
        empty = self.dir / "empty.sqlite3"
        self.ok("--db", str(empty), "init")
        self.assertIn("business add", self.ok("--db", str(empty), "status"))
        self.ok("--db", str(empty), "business", "add", "X")
        self.assertIn("account add", self.ok("--db", str(empty), "status"))

    def test_many_rows_in_one_review_decision(self) -> None:
        lines = ["Date,Description,Amount"]
        lines.extend(f"2025-02-{(i % 28) + 1:02d},SAME MERCHANT,-{i + 1}.00" for i in range(1500))
        self.ok("account", "map", "Chk", "--date-format", "%Y-%m-%d")
        self.ok("import", "Chk", str(self.write("m.csv", "\n".join(lines) + "\n")))
        self.assertIn("Booked 1500", self.ok("review", stdin="r 6150\n"))


class ErrorsAreBooksErrors(unittest.TestCase):
    def test_restore_of_nothing(self) -> None:
        with self.assertRaises(BooksError):
            safety.restore(Path("x.sqlite3"), Path("definitely-missing.sqlite3"))
