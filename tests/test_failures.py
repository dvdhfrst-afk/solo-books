"""How the program fails: plainly, with the right exit status, losing nothing. Hostile input included."""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from solo_books import cli, db, files, rules
from solo_books.csvmap import MAX_TEXT, Mapping, decode, parse_export, suggest_mapping
from solo_books.errors import BooksError
from solo_books.text import clean_cell, printable, slug, spreadsheet_cell
from tests.helpers import REPO_ROOT, BooksCase, read_csv

HEADER = b"Date,Description,Amount\n"


class CleanFailure(BooksCase):
    """A person's mistake gets a sentence and exit status 2, never a stack trace."""

    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok(
            "account", "add", "Chk", "--kind", "checking", "--opening-balance", "10", "--opening-date", "2025-01-01",
            "--date-format", "%Y-%m-%d",
        )

    def raw(self, name: str, data: bytes) -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def refuses(self, *args: str, says: str = "") -> str:
        code, out, err = self.run_cli(*args)
        self.assertEqual(code, 2, f"{args}: {out} {err}")
        self.assertNotIn("Traceback", err + out)
        if says:
            self.assertIn(says, err)
        return err

    def assert_refused_import(self, data: bytes, says: str) -> None:
        before = self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self.refuses("import", "Chk", str(self.raw("bad.csv", data)), says=says)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], before)

    def test_files_that_are_not_exports(self) -> None:
        self.assert_refused_import(b"", "no header row")
        self.assert_refused_import(b"PK\x03\x04rest of a workbook", "Excel")
        self.assert_refused_import(b"\xd0\xcf\x11\xe0old workbook", "Excel")
        self.assert_refused_import(b"%PDF-1.7 statement", "PDF")
        self.assert_refused_import(b"<!DOCTYPE html><html>Sign in</html>", "web page")
        self.assert_refused_import(b"SQLite format 3\x00", "database")
        self.assert_refused_import(b"!Type:Bank\nD01/02/2025\n", "QIF")
        self.assert_refused_import(os.urandom(64).replace(b"\x00", b"a") + b"\x00" * 300, "binary")

    def test_a_cell_too_large_for_the_csv_reader(self) -> None:
        self.assert_refused_import(HEADER + b"2025-01-02," + b"x" * 200000 + b",-5.00\n", "well-formed")

    def test_a_quote_that_never_closes_stops_the_import(self) -> None:
        self.assert_refused_import(HEADER + b'2025-01-02,"CAFE,-5.00\n2025-01-03,TEA,-1.00\n', "never closes")

    def test_a_folder_and_a_missing_file(self) -> None:
        self.refuses("import", "Chk", str(self.dir))
        self.refuses("import", "Chk", str(self.dir / "missing.csv"), says="cannot read")
        self.refuses("mapping", "suggest", str(self.dir / "missing.csv"), says="cannot read")

    def test_amounts_and_dates_too_wild_to_be_rows(self) -> None:
        out = self.fails("import", "Chk", str(self.raw("x.csv", HEADER + b"2025-01-02,CAFE,99999999999999999999.00\n")))
        self.assertIn("too large", out)
        out = self.fails("import", "Chk", str(self.raw("y.csv", HEADER + b"0001-01-01,CAFE,-5.00\n9999-12-31,CAFE,-5.00\n")))
        self.assertIn("reads as the year 1", out)
        self.assertIn("reads as the year 9999", out)

    def test_absurd_numbers_on_the_command_line(self) -> None:
        huge = "9" * 30
        for args in (
            ("txn", "show", huge),
            ("txn", "delete", huge),
            ("categorize", "Meals", huge),
            ("reconcile", "show", huge),
            ("imports", "undo", huge),
            ("rules", "delete", huge),
            ("rules", "add", "--contains", "x", "--category", "Meals", "--priority", huge),
            ("txn", "add", "Chk", "--date", "2025-01-02", "--amount", huge, "--description", "d"),
        ):
            self.refuses(*args)

    def test_years_out_of_range(self) -> None:
        self.refuses("report", "pnl", "--period", "0000", says="1900")
        self.refuses("report", "pnl", "--period", "9999-Q1", says="1900")
        self.refuses("report", "preparer", "--year", "0", "--out", str(self.dir / "p"))
        self.refuses("report", "schedule-c", "--year", "99999")
        self.refuses("inventory", "set", "--year", "9" * 30, "--beginning", "1", "--ending", "2")
        self.refuses("txn", "add", "Chk", "--date", "0001-01-01", "--amount", "1", "--description", "d")

    def test_output_places_that_cannot_be_written(self) -> None:
        self.refuses("report", "pnl", "--period", "2025", "-o", str(self.dir / "no" / "such" / "x.csv"), says="does not exist")
        self.refuses("report", "pnl", "--period", "2025", "-o", str(self.dir), says="folder")
        a_file = self.raw("afile", b"x")
        self.refuses("report", "preparer", "--year", "2025", "--out", str(a_file), says="is a file")
        self.refuses("backup", str(self.dir / "no" / "dir" / "b.sqlite3"), says="does not exist")
        self.refuses("backup", str(self.dir), says="already exists")

    def test_a_report_can_never_land_on_the_books(self) -> None:
        self.refuses("report", "pnl", "--period", "2025", "-o", str(self.db_path), says="books file")
        self.refuses("report", "pnl", "--period", "2025", "--format", "csv", "-o", str(self.db_path), says="books file")
        self.refuses("rules", "export", "-o", str(self.db_path), says="books file")
        self.refuses("mapping", "suggest", str(self.raw("x.csv", HEADER)), "-o", str(self.db_path), says="books file")
        self.assertIn("Business A", self.ok("business", "list"))

    def test_bad_mapping_files(self) -> None:
        for name, text, says in (
            ("a.json", '{"delimiter": null}', "delimiter"),
            ("b.json", '{"skip_statuses": [1, 2]}', "skip_statuses"),
            ("c.json", "[1, 2]", "JSON object"),
            ("d.json", '{"skip_lines": true}', "skip_lines"),
            ("e.json", '{"date_format": 5}', "date_format"),
            ("f.json", '{"encoding": "nonsense"}', "encoding"),
            ("g.json", '{"column_names": "Date"}', "column_names"),
            ("h.json", '{"bogus": 1}', "bogus"),
            ("i.json", "{not json", "JSON"),
        ):
            self.refuses("account", "map", "Chk", "--mapping", str(self.write(name, text)), says=says)
        self.refuses("account", "map", "Chk", "--mapping", str(self.raw("j.json", b"\xff\xfe")), says="UTF-8")
        self.refuses("account", "map", "Chk", "--encoding", "nonsense", says="encoding")

    def test_bad_rules_files_add_nothing(self) -> None:
        for name, data in (
            ("a.csv", b"match,pattern,category\ncontains,a\x00b,Meals\n"),
            ("b.csv", b"match,pattern,category\nregex,(a+)+$,Meals\n"),
            ("c.csv", b"match,pattern,category\ncontains,OK,Meals\nregex,(,Meals\n"),
            ("d.csv", b""),
            ("e.csv", b"match,pattern,category\ncontains," + b"x" * 400 + b",Meals\n"),
        ):
            self.refuses("rules", "import", str(self.raw(name, data)))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0], 0)

    def test_regular_expressions_that_could_hang_are_refused(self) -> None:
        for pattern in ("(a+)+$", "(a*)*b", "(.*)+x", "(ab+c)*", "([a-z]+)+$", "(x+){2,}"):
            with self.assertRaises(BooksError, msg=pattern):
                rules.check_regex(pattern)
        for pattern in ("AMAZON|AMZN", "^STORE [0-9]+$", "(CAFE|TEA) SHOP", r"\d+", "A+B*"):
            rules.check_regex(pattern)

    def test_argument_errors_use_status_two_and_no_traceback(self) -> None:
        for args in (("nosuchcommand",), (), ("txn", "show", "abc"), ("web", "--port", "99999"), ("txn", "list", "--limit", "0")):
            code, out, err = self.run_cli(*args)
            self.assertEqual(code, 2, args)
            self.assertNotIn("Traceback", err + out)

    def test_empty_terminal_input_ends_review_cleanly(self) -> None:
        self.ok("txn", "add", "Chk", "--date", "2025-01-02", "--amount", "-1.00", "--description", "CAFE")
        self.assertIn("still waiting", self.ok("review", stdin=""))

    def test_ctrl_c_in_review_keeps_what_was_booked(self) -> None:
        self.ok("txn", "add", "Chk", "--date", "2025-01-02", "--amount", "-1.00", "--description", "CAFE")
        self.ok("txn", "add", "Chk", "--date", "2025-01-03", "--amount", "-2.00", "--description", "TEA")
        answers = iter(["6150"])

        def typed(_prompt: str) -> str:
            try:
                return next(answers)
            except StopIteration:
                raise KeyboardInterrupt from None

        with mock.patch("builtins.input", typed):
            code, _, err = self.run_cli("review")
        self.assertEqual(code, 130)
        self.assertIn("stopped", err)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NOT NULL").fetchone()[0], 1)

    def test_a_bug_gets_a_sentence_and_its_own_status(self) -> None:
        with mock.patch("solo_books.cli.cmd_status", side_effect=ValueError("boom")):
            code, out, err = self.run_cli("status")
        self.assertEqual(code, 3)
        self.assertIn("bug", err)
        self.assertNotIn("Traceback", err + out)

    def test_the_debug_setting_shows_the_real_error(self) -> None:
        with mock.patch("solo_books.cli.cmd_status", side_effect=ValueError("boom")), mock.patch.dict(os.environ, {cli.DEBUG_ENV_VAR: "1"}):
            with self.assertRaises(ValueError):
                self.run_cli("status")

    def test_every_command_has_help(self) -> None:
        import argparse

        def walk(parser, path):
            yield path
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for name, sub in action.choices.items():
                        yield from walk(sub, [*path, name])

        seen = 0
        for path in walk(cli.build_parser(), []):
            code, out, _ = self.run_cli(*path, "--help")
            self.assertEqual(code, 0, path)
            self.assertIn("usage:", out, path)
            seen += 1
        self.assertGreater(seen, 40)

    def test_version(self) -> None:
        code, out, _ = self.run_cli("--version")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^solo-books \d+\.\d+\.\d+")


class Encodings(BooksCase):
    def test_utf16_with_and_without_a_mark(self) -> None:
        text = "Date,Description,Amount\n01/02/2025,CAF\u00c9,-5.00\n"
        for encoded in (text.encode("utf-16"), text.encode("utf-16-le"), text.encode("utf-16-be"), text.encode("utf-32")):
            path = self.dir / "x.csv"
            path.write_bytes(encoded)
            self.assertEqual(parse_export(path, Mapping()).rows[0].description, "CAF\u00c9")

    def test_windows_1252_and_utf8_with_a_mark(self) -> None:
        text = "Date,Description,Amount\n01/02/2025,CAF\u00c9,-5.00\n"
        for encoded in (text.encode("cp1252"), text.encode("utf-8-sig")):
            path = self.dir / "x.csv"
            path.write_bytes(encoded)
            self.assertEqual(parse_export(path, Mapping()).rows[0].description, "CAF\u00c9")

    def test_non_ascii_descriptions_and_names_round_trip(self) -> None:
        self.ok("business", "add", "\u30ab\u30d5\u30a7\u5546\u4f1a")
        self.ok("account", "add", "\u53e3\u5ea7", "--kind", "checking")
        path = self.write("x.csv", "Date,Description,Amount\n01/02/2025,\u6771\u4eac\u30ab\u30d5\u30a7 CAF\u00c9,-5.00\n")
        self.ok("account", "map", "\u53e3\u5ea7", "--date-format", "%m/%d/%Y")
        self.ok("import", "\u53e3\u5ea7", str(path))
        self.assertIn("\u6771\u4eac", self.ok("txn", "list", "--search", "\u6771\u4eac"))
        out_dir = self.dir / "\u51fa\u529b"
        self.ok("report", "preparer", "--year", "2025", "--out", str(out_dir), "--lines-year", "2025")
        names = [path.name for path in out_dir.iterdir()]
        self.assertIn("\u30ab\u30d5\u30a7\u5546\u4f1a-transactions-2025.csv", names)
        detail = read_csv(out_dir / "\u30ab\u30d5\u30a7\u5546\u4f1a-transactions-2025.csv")
        self.assertIn("\u6771\u4eac", detail[0]["description"])

    def test_a_stated_encoding_that_does_not_fit_is_a_plain_error(self) -> None:
        with self.assertRaises(BooksError):
            decode(b"\xff\xfe\xfa", "x.csv", "ascii")


class Hostile(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "Chk", "--kind", "checking", "--date-format", "%Y-%m-%d")

    def bring_in(self, description: bytes) -> None:
        path = self.dir / "h.csv"
        path.write_bytes(HEADER + b"2025-01-02," + description + b",-5.00\n")
        self.ok("import", "Chk", str(path))

    def test_terminal_escapes_are_gone_before_they_are_stored(self) -> None:
        self.bring_in(b"\x1b[31mEVIL\x1b]0;pwned\x07 CAFE")
        stored = self.conn.execute("SELECT description FROM transactions").fetchone()[0]
        self.assertNotIn("\x1b", stored)
        self.assertNotIn("\x07", stored)
        self.assertNotIn("\x1b", self.ok("txn", "list"))

    def test_escapes_already_in_old_books_never_reach_the_screen(self) -> None:
        self.ok("txn", "add", "Chk", "--date", "2025-01-02", "--amount", "-1.00", "--description", "CAFE")
        self.conn.execute("UPDATE transactions SET description = 'A\x1b[2JB', memo = '\x1b]0;x\x07'")
        for args in (("txn", "list"), ("txn", "show", "1"), ("review", "--list"), ("review", "--groups")):
            self.assertNotIn("\x1b", self.ok(*args), args)

    def test_right_to_left_overrides_cannot_disguise_text(self) -> None:
        self.assertEqual(clean_cell("PAY\u202eELPMAXE"), "PAYELPMAXE")
        self.assertEqual(printable("a\x1bb\nc\td"), "ab\nc\td")

    def test_line_breaks_in_a_cell_become_one_line(self) -> None:
        self.bring_in(b'"LINE ONE\nLINE TWO"')
        self.assertEqual(self.conn.execute("SELECT description FROM transactions").fetchone()[0], "LINE ONE LINE TWO")

    def test_an_oversized_cell_is_a_bad_row(self) -> None:
        path = self.dir / "h.csv"
        path.write_bytes(HEADER + b"2025-01-02," + b"x" * (MAX_TEXT + 1) + b",-5.00\n")
        self.assertIn("longer than", self.fails("import", "Chk", str(path)))

    def test_formulas_do_not_survive_into_any_csv(self) -> None:
        self.bring_in(b'"=HYPERLINK(""http://example.invalid"")"')
        self.ok("categorize", "6150", "1")
        self.ok("txn", "note", "1", "@SUM(A1)")
        for text in (
            self.ok("txn", "list", "--format", "csv"),
        ):
            rows = list(csv.DictReader(io.StringIO(text)))
            self.assertTrue(rows[0]["description"].startswith("'="))
            self.assertTrue(rows[0]["note"].startswith("'@"))
            self.assertEqual(rows[0]["amount"], "-5.00")
        self.ok("business", "rename", "Business A", "=cmd()")
        for command in (("report", "pnl"), ("report", "balances")):
            rows = list(csv.DictReader(io.StringIO(self.ok(*command, "--period", "2025", "--format", "csv"))))
            self.assertTrue(rows[0]["business"].startswith("'="), command)
            self.assertFalse(any(row["amount"].startswith("'") for row in rows), command)

    def test_a_rule_made_from_a_hostile_merchant_round_trips_safely(self) -> None:
        self.ok("rules", "add", "--contains", "=HOSTILE", "--category", "Meals")
        text = self.ok("rules", "export")
        self.assertIn("'=HOSTILE", text)
        self.assertEqual(self.conn.execute("SELECT pattern FROM rules").fetchone()[0], "=HOSTILE")
        path = self.write("r.csv", text)
        self.ok("business", "add", "Business B")
        self.ok("rules", "import", str(path), "--business", "Business B")
        patterns = [row[0] for row in self.conn.execute("SELECT pattern FROM rules")]
        self.assertEqual(patterns, ["=HOSTILE", "=HOSTILE"])

    def test_spreadsheet_cells(self) -> None:
        self.assertEqual(spreadsheet_cell("-15.00"), "-15.00")
        self.assertEqual(spreadsheet_cell("-1,234.50"), "-1,234.50")
        self.assertEqual(spreadsheet_cell("-15.00+cmd"), "'-15.00+cmd")
        self.assertEqual(spreadsheet_cell(" =1+1"), "' =1+1")
        self.assertEqual(spreadsheet_cell("Total"), "Total")

    def test_names_cannot_escape_the_folder(self) -> None:
        self.assertEqual(slug("../../evil"), "evil")
        self.assertEqual(slug("..\\..\\evil"), "evil")
        self.assertEqual(slug("/etc/passwd"), "etc-passwd")
        self.assertEqual(slug("C:\\Windows"), "c-windows")
        self.ok("business", "rename", "Business A", "../../evil")
        out_dir = self.dir / "p"
        self.ok("report", "preparer", "--year", "2025", "--out", str(out_dir))
        self.assertTrue((out_dir / "evil-transactions-2025.csv").exists())
        self.assertEqual(sorted(path.parent for path in out_dir.iterdir())[0], out_dir)
        self.assertFalse((self.dir.parent / "evil-transactions-2025.csv").exists())

    def test_two_names_with_one_slug_do_not_overwrite_each_other(self) -> None:
        self.ok("business", "add", "A B")
        self.ok("business", "add", "A-B")
        self.ok("business", "add", "\u2603")
        self.ok("business", "add", "!!!")
        out_dir = self.dir / "p"
        self.ok("report", "preparer", "--year", "2025", "--out", str(out_dir))
        count = len([path for path in out_dir.iterdir() if "-schedule-c-" in path.name])
        self.assertEqual(count, 5)

    def test_sql_in_a_name_or_search_is_only_text(self) -> None:
        attack = "'; DROP TABLE transactions; --"
        self.bring_in(b"CAFE")
        self.ok("business", "rename", "Business A", attack)
        self.ok("account", "rename", "Chk", attack)
        self.ok("txn", "note", "1", attack)
        self.ok("txn", "list", "--search", attack)
        self.ok("rules", "add", "--contains", attack, "--category", "Meals")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)


class AtomicWrites(BooksCase):
    def test_a_failed_write_leaves_the_old_file_and_no_scraps(self) -> None:
        target = self.dir / "report.csv"
        target.write_bytes(b"old")
        with mock.patch("solo_books.files.os.fsync", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(BooksError) as caught:
                files.write_atomic(target, b"new")
        self.assertIn("No space left", str(caught.exception))
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual([path.name for path in self.dir.iterdir() if path.name.endswith(".partial")], [])

    def test_a_report_file_arrives_whole(self) -> None:
        self.ok("business", "add", "Business A")
        target = self.dir / "r.txt"
        self.assertIn("Wrote", self.ok("report", "pnl", "--period", "2025", "-o", str(target)))
        self.assertTrue(target.read_text(encoding="utf-8-sig").startswith("Profit and loss"))

    def test_preparer_folder_is_not_started_when_a_report_cannot_be_made(self) -> None:
        self.ok("business", "add", "Business A")
        out_dir = self.dir / "p"
        self.fails("report", "preparer", "--year", "2020", "--out", str(out_dir))
        self.assertFalse(out_dir.exists())


class OutputTrouble(BooksCase):
    def run_with_stdout(self, stream) -> int:
        with mock.patch("sys.stdout", stream), mock.patch("sys.stderr", io.StringIO()) as err:
            code = cli.main(["--db", str(self.db_path), "status"])
        self.said = err.getvalue()
        return code

    def test_a_full_disk_under_a_redirect_is_reported(self) -> None:
        class Full(io.StringIO):
            def flush(self) -> None:
                raise OSError(28, "No space left on device")

        self.assertEqual(self.run_with_stdout(Full()), 2)
        self.assertIn("No space left", self.said)

    def test_a_reader_that_left_is_not_an_error(self) -> None:
        class Gone(io.StringIO):
            def flush(self) -> None:
                raise BrokenPipeError(32, "Broken pipe")

        self.assertEqual(self.run_with_stdout(Gone()), 0)

    def test_redirected_text_stays_whole_on_any_code_page(self) -> None:
        self.ok("business", "add", "\u6771\u4eac Business")
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
        env.pop("PYTHONIOENCODING", None)
        done = subprocess.run(
            [sys.executable, "-m", "solo_books", "--db", str(self.db_path), "business", "list"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(done.returncode, 0)
        self.assertIn("\u6771\u4eac Business", done.stdout.decode("utf-8"))

    def test_awkward_folder_names_work_everywhere(self) -> None:
        folder = self.dir / "\u65e5\u672c #1 %41 it's"
        folder.mkdir()
        books = folder / "my books.sqlite3"
        for args in (("init",), ("business", "add", "X"), ("check",), ("backup",), ("salvage", str(folder / "s.sqlite3")), ("status",)):
            self.assertEqual(self.run_cli("--db", str(books), *args)[0], 0, args)
        kept = [path for path in folder.iterdir() if "-backup-" in path.name][0]
        self.assertEqual(self.run_cli("--db", str(books), "restore", str(kept))[0], 0)


class Interrupted(BooksCase):
    """A real process, really killed, in the middle of a real import."""

    def test_a_killed_import_leaves_all_or_nothing_and_healthy_books(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "Chk", "--kind", "checking", "--date-format", "%Y-%m-%d")
        lines = ["Date,Description,Amount"]
        lines.extend(f"2025-01-{(i % 28) + 1:02d},MERCHANT NUMBER {i},-{i % 90 + 1}.25" for i in range(30000))
        path = self.write("big.csv", "\n".join(lines) + "\n")
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
        process = subprocess.Popen(
            [sys.executable, "-m", "solo_books", "--db", str(self.db_path), "import", "Chk", str(path)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Kill it once its write has started, shown by the journal file beside the books.
        journal = Path(str(self.db_path) + "-journal")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and process.poll() is None and not journal.exists():
            time.sleep(0.002)
        process.kill()
        process.wait()
        count = self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        self.assertIn(count, (0, 30000))
        imports = self.conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        self.assertEqual(imports, 1 if count else 0)
        self.assertIn("no problems found", self.ok("check"))
        # And the person can simply run it again.
        self.ok("import", "Chk", str(path))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 30000)


class Locked(BooksCase):
    def test_books_in_use_give_a_plain_message(self) -> None:
        self.ok("business", "add", "Business A")
        blocker = sqlite3.connect(str(self.db_path), isolation_level=None)
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            with mock.patch("solo_books.db.BUSY_SECONDS", 0):
                code, _, err = self.run_cli("business", "add", "Business B")
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        self.assertEqual(code, 2)
        self.assertIn("busy", err)
        self.ok("business", "add", "Business B")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can write to anything")
    def test_read_only_books_can_be_read_and_say_why_they_cannot_be_written(self) -> None:
        self.ok("business", "add", "Business A")
        self.conn.close()
        os.chmod(self.db_path, stat.S_IREAD)
        self.addCleanup(os.chmod, self.db_path, stat.S_IREAD | stat.S_IWRITE)
        self.assertIn("Business A", self.ok("business", "list"))
        code, _, err = self.run_cli("business", "add", "Business B")
        self.assertEqual(code, 2)
        self.assertIn("read-only", err)


class WarningsWorthHaving(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok(
            "account", "add", "Chk", "--kind", "checking", "--opening-balance", "10", "--opening-date", "2025-01-10",
            "--date-format", "%Y-%m-%d",
        )

    def test_rows_before_the_opening_date_are_called_out_at_import(self) -> None:
        path = self.write("x.csv", "Date,Description,Amount\n2025-01-02,CAFE,-5.00\n")
        self.assertIn("before this account's opening date", self.ok("import", "Chk", str(path)))

    def test_rows_dated_far_ahead_are_called_out(self) -> None:
        path = self.write("x.csv", "Date,Description,Amount\n2190-01-11,CAFE,-5.00\n")
        self.assertIn("dated after today", self.ok("import", "Chk", str(path)))

    def test_a_file_with_only_a_header_says_so(self) -> None:
        path = self.write("x.csv", "Date,Description,Amount\n")
        self.assertIn("no rows", self.ok("import", "Chk", str(path)))

    def test_thousands_of_row_warnings_do_not_bury_the_screen(self) -> None:
        lines = ["Date,Description,Amount"]
        lines.extend(f"2025-02-{(i % 2) + 1:02d},CAFE 0{i % 2},-5.00" for i in range(2))
        first = self.write("a.csv", "\n".join(lines) + "\n")
        self.ok("import", "Chk", str(first))
        many = ["Date,Description,Amount"]
        many.extend(f"2025-02-0{(i % 3) + 1},CAFE {'X' * (i + 1)} 1,-5.00" for i in range(0))
        repeats = ["Date,Description,Amount"]
        repeats.extend(f"2025-02-02,CAFE 1{i:03d},-5.00" for i in range(30))
        out = self.ok("import", "Chk", str(self.write("b.csv", "\n".join(repeats) + "\n")))
        self.assertIn("more like these", out)
        self.assertLess(out.count("Possible duplicate"), 12)
