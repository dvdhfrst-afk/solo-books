"""The command line as a person meets it: messages, exit codes, files."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from solo_books import db
from solo_books.cli import main
from tests.helpers import REPO_ROOT, BooksCase, read_csv


class Basics(BooksCase):
    def test_runs_as_a_module_from_the_repo_root(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "solo_books", "--version"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("solo-books", result.stdout)

    def test_missing_books_are_an_error_not_empty_books(self) -> None:
        missing = self.dir / "nope.sqlite3"
        self.db_path = missing
        self.assertIn("no books at", self.fails("status"))
        self.assertFalse(missing.exists())

    def test_init_creates_and_never_clobbers(self) -> None:
        path = self.dir / "sub" / "new.sqlite3"
        with mock.patch("builtins.print"):
            self.assertEqual(main(["--db", str(path), "init"]), 0)
            self.assertEqual(main(["--db", str(path), "business", "add", "Business A"]), 0)
            self.assertEqual(main(["--db", str(path), "init"]), 0)
            conn = db.connect(path)
            self.addCleanup(conn.close)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0], 1)

    def test_db_flag_works_after_the_command_too(self) -> None:
        with mock.patch("builtins.print"):
            self.assertEqual(main(["status", "--db", str(self.db_path)]), 0)

    def test_environment_variable_names_the_books(self) -> None:
        with mock.patch.dict(os.environ, {db.DB_ENV_VAR: str(self.db_path)}):
            self.assertEqual(db.resolve_db_path(None), self.db_path)
            self.assertEqual(db.resolve_db_path("other"), Path("other"))

    def test_a_file_that_is_not_books_is_refused(self) -> None:
        self.db_path = self.write("junk.sqlite3", "this is not a database, just text that is long enough\n" * 50)
        self.assertIn("not a solo-books file", self.fails("status"))

    def test_a_newer_file_is_refused(self) -> None:
        self.conn.execute("PRAGMA user_version = 999")
        self.assertIn("newer", self.fails("status"))

    def test_backup_makes_a_working_copy_and_will_not_overwrite(self) -> None:
        self.ok("business", "add", "Business A")
        copy = self.dir / "copy.sqlite3"
        self.ok("backup", str(copy))
        conn = db.connect(copy)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT name FROM businesses").fetchone()[0], "Business A")
        self.assertIn("already exists", self.fails("backup", str(copy)))


class Workflow(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "A Checking", "--kind", "checking", "--opening-balance", "100.00", "--opening-date", "2025-01-01", "--date-col", "Date")
        self.csv = self.write(
            "a.csv",
            "Date,Description,Amount\n01/02/2025,BLUE FERN CAFE 0102,-4.50\n01/03/2025,CLIENT PAYMENT,100.00\n",
        )

    def test_single_business_needs_no_flag(self) -> None:
        self.assertIn("Business A", self.ok("chart", "list"))

    def test_two_businesses_need_the_flag(self) -> None:
        self.ok("business", "add", "Business B")
        self.assertIn("--business", self.fails("chart", "list"))

    def test_duplicate_names_refused(self) -> None:
        self.assertIn("already", self.fails("business", "add", "business a"))
        self.assertIn("already", self.fails("account", "add", "a checking", "--kind", "checking"))

    def test_opening_balance_needs_its_date(self) -> None:
        self.assertIn("together", self.fails("account", "add", "X", "--kind", "checking", "--opening-balance", "1.00"))

    def test_import_prints_counts_and_dry_run_prints_samples(self) -> None:
        out = self.ok("import", "A Checking", str(self.csv), "--dry-run")
        self.assertIn("DRY RUN", out)
        self.assertIn("BLUE FERN CAFE", out)
        self.assertIn("-4.50", out)
        out = self.ok("import", "A Checking", str(self.csv))
        self.assertIn("new:                2", out)
        out = self.ok("import", "A Checking", str(self.csv))
        self.assertIn("already in books:   2", out)
        self.assertIn("195.50", self.ok("account", "list"))

    def test_bad_import_exits_nonzero_with_lines(self) -> None:
        bad = self.write("bad.csv", "Date,Description,Amount\nnope,X,-1.00\n")
        self.assertIn("line 2", self.fails("import", "A Checking", str(bad)))

    def test_mapping_suggest_writes_a_file_import_accepts(self) -> None:
        mapping = self.dir / "map.json"
        self.ok("mapping", "suggest", str(self.csv), "-o", str(mapping))
        self.ok("account", "map", "A Checking", "--mapping", str(mapping))
        self.ok("import", "A Checking", str(self.csv))
        self.assertIn("Date", self.ok("account", "show", "A Checking"))

    def test_account_map_flags_change_part_of_a_mapping(self) -> None:
        self.ok("account", "map", "A Checking", "--date-col", "Posted", "--invert-amount")
        out = self.ok("account", "show", "A Checking")
        self.assertIn('"date_column": "Posted"', out)
        self.assertIn('"invert_amount": true', out)
        self.ok("account", "map", "A Checking", "--debit-col", "Out", "--credit-col", "In", "--no-invert-amount")
        out = self.ok("account", "show", "A Checking")
        self.assertIn('"amount_column": null', out)
        self.assertIn('"invert_amount": false', out)
        self.assertIn("nothing to set", self.fails("account", "map", "A Checking"))

    def test_review_and_categorize_by_command(self) -> None:
        self.ok("import", "A Checking", str(self.csv))
        out = self.ok("review", "--groups")
        self.assertIn("BLUE FERN CAFE", out)
        self.assertIn("2 row(s) in 2 group(s)", out)
        self.assertIn("2 row(s) waiting", self.ok("review", "--list"))
        self.ok("categorize", "Meals", "1")
        self.ok("categorize", "4000", "2")
        self.assertIn("Nothing waiting", self.ok("review"))
        self.ok("categorize", "--clear", "1", "2")
        self.assertIn("2 row(s) waiting", self.ok("review", "--list"))

    def test_review_interactive_through_stdin(self) -> None:
        self.ok("import", "A Checking", str(self.csv))
        out = self.ok("review", stdin="r 6150\ns\nq\n")
        self.assertIn("Booked 1, skipped 1, 1 still waiting", out)
        self.assertIn("BLUE FERN CAFE", self.ok("rules", "list"))

    def test_rules_cli_round(self) -> None:
        self.ok("import", "A Checking", str(self.csv))
        out = self.ok("rules", "add", "--contains", "FERN", "--category", "Meals")
        self.assertIn("booked 1", out)
        self.assertIn("6150 Meals", self.ok("rules", "test", "BLUE FERN CAFE 9"))
        code, out, _ = self.run_cli("rules", "test", "SOMETHING ELSE")
        self.assertEqual(code, 1)
        self.assertIn("No rule", out)
        self.assertIn("FERN", self.ok("rules", "export"))
        self.assertIn("booked 0", self.ok("rules", "apply"))
        self.ok("rules", "delete", "1")
        self.assertIn("no rule", self.fails("rules", "delete", "1"))
        self.assertIn("exactly one", self.fails("rules", "add", "--category", "Meals"))

    def test_txn_commands(self) -> None:
        self.ok("import", "A Checking", str(self.csv))
        self.assertIn("2 row(s), net 95.50", self.ok("txn", "list"))
        self.assertIn("1 row(s)", self.ok("txn", "list", "--search", "FERN"))
        self.assertIn("2 row(s)", self.ok("txn", "list", "--uncategorized", "--period", "2025-01"))
        self.assertIn("0 row(s)", self.ok("txn", "list", "--period", "2025-02"))
        self.ok("txn", "note", "1", "lunch with a client")
        csv_path = self.dir / "rows.csv"
        self.ok("txn", "list", "--format", "csv", "-o", str(csv_path))
        rows = read_csv(csv_path)
        self.assertEqual(rows[0]["note"], "lunch with a client")
        self.assertEqual(rows[0]["category"], "UNCATEGORIZED")
        self.ok("txn", "add", "A Checking", "--date", "2025-01-05", "--amount", "-1.00", "--description", "CASH", "--category", "6290")
        self.ok("txn", "delete", "3")
        self.assertIn("no transaction", self.fails("txn", "delete", "3"))
        self.assertIn("zero", self.fails("txn", "add", "A Checking", "--date", "2025-01-05", "--amount", "0", "--description", "X"))
        self.assertIn("not a date", self.fails("txn", "add", "A Checking", "--date", "soon", "--amount", "1", "--description", "X"))
        self.assertIn("not an amount", self.fails("txn", "add", "A Checking", "--date", "2025-01-05", "--amount", "lots", "--description", "X"))

    def test_csv_export_defuses_formulas_in_descriptions(self) -> None:
        self.ok("txn", "add", "A Checking", "--date", "2025-01-05", "--amount", "-1.00", "--description", "=HYPERLINK(1)")
        csv_path = self.dir / "rows.csv"
        self.ok("txn", "list", "--format", "csv", "-o", str(csv_path))
        self.assertEqual(read_csv(csv_path)[0]["description"], "'=HYPERLINK(1)")

    def test_reconcile_commands(self) -> None:
        self.ok("import", "A Checking", str(self.csv))
        code, out, _ = self.run_cli("reconcile", "run", "A Checking", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "100.00", "--end", "200.00")
        self.assertEqual(code, 1)
        self.assertIn("DOES NOT MATCH", out)
        self.assertIn("does not tie", self.fails("reconcile", "finish", "1"))
        out = self.ok("reconcile", "run", "A Checking", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "100.00", "--end", "195.50")
        self.assertIn("reconcile finish 1", out)
        self.ok("reconcile", "finish", "1")
        self.assertIn("finished", self.ok("reconcile", "list"))
        self.assertIn("ties to the cent", self.ok("reconcile", "show", "1"))
        self.assertIn("finished reconciliation", self.fails("txn", "delete", "1"))
        self.assertIn("2025-01-31", self.ok("status"))
        self.ok("reconcile", "reopen", "1")
        self.ok("reconcile", "forget", "1")
        self.assertIn("no reconciliation", self.fails("reconcile", "show", "1"))

    def test_imports_list_and_undo(self) -> None:
        self.ok("import", "A Checking", str(self.csv))
        self.assertIn("a.csv", self.ok("imports", "list"))
        self.assertIn("Removed 2", self.ok("imports", "undo", "1"))
        self.assertIn("undone", self.ok("imports", "list", "--account", "A Checking"))

    def test_chart_commands(self) -> None:
        self.ok("chart", "add", "6300", "Co-working", "--kind", "expense", "--line", "20b", "--year", "2025")
        self.assertIn("Co-working", self.ok("chart", "list", "--year", "2025"))
        self.ok("chart", "map", "6300", "--line", "48")
        self.ok("chart", "rename", "6300", "Shared desks")
        self.ok("chart", "map", "6300", "--none")
        self.ok("chart", "retire", "6300")
        self.assertIn("retired", self.ok("chart", "list"))
        self.assertIn("no line", self.fails("chart", "map", "6200", "--line", "999"))

    def test_output_files_for_reports(self) -> None:
        self.ok("categorize", "--clear")
        path = self.dir / "pnl.csv"
        self.assertIn("Wrote", self.ok("report", "pnl", "--period", "2025", "--format", "csv", "-o", str(path)))
        self.assertTrue(any("not tax or accounting advice" in row["category"] for row in read_csv(path)))
        path = self.dir / "c.csv"
        self.assertIn("Wrote", self.ok("report", "schedule-c", "--year", "2025", "--format", "csv", "-o", str(path)))
        text_path = self.dir / "c.txt"
        self.assertIn("Wrote", self.ok("report", "schedule-c", "--year", "2025", "-o", str(text_path)))
        self.assertTrue(text_path.read_text(encoding="utf-8-sig").startswith("Schedule C rollup"))

    def test_preparer_export_warns_about_open_rows(self) -> None:
        self.ok("import", "A Checking", str(self.csv))
        out = self.ok("report", "preparer", "--year", "2025", "--out", str(self.dir / "out"))
        self.assertIn("2 uncategorized", out)
        rows = read_csv(self.dir / "out" / "business-a-transactions-2025.csv")
        self.assertEqual({row["category"] for row in rows}, {"UNCATEGORIZED"})
