"""Shared test setup: a throwaway books file and a way to run commands against it."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import List, Tuple
from unittest import mock

from solo_books import books, db
from solo_books.cli import main
from solo_books.csvmap import Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKED = REPO_ROOT / "tests" / "fixtures" / "worked"


class BooksCase(unittest.TestCase):
    """Each test gets its own folder and its own empty books."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="solo-books-test-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.db_path = self.dir / "books.sqlite3"
        self.conn = db.connect(self.db_path, create=True)
        self.addCleanup(self.conn.close)

    def run_cli(self, *args: str, stdin: str = "") -> Tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch("sys.stdin", io.StringIO(stdin)):
                try:
                    code = main(["--db", str(self.db_path), *args])
                except SystemExit as exc:
                    code = exc.code
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args: str, stdin: str = "") -> str:
        code, out, err = self.run_cli(*args, stdin=stdin)
        self.assertEqual(code, 0, f"{args} failed: {err or out}")
        return out

    def fails(self, *args: str) -> str:
        code, out, err = self.run_cli(*args)
        self.assertNotEqual(code, 0, f"{args} should have failed: {out}")
        return err + out

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def business(self, name: str = "Business A"):
        return books.add_business(self.conn, name)

    def account(self, business, name: str = "A Checking", kind: str = "checking", mapping: Mapping = None, opening=None):
        mapping = mapping or Mapping()
        opening_date = date(2025, 1, 1) if opening is not None else None
        return books.add_account(
            self.conn, business, name, kind, "Example Bank", opening, opening_date, mapping.to_json()
        )

    def category(self, business, code: str):
        return books.find_category(self.conn, business["id"], code)


def read_csv(path: Path) -> List[dict]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_csv(text: str) -> List[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def worked_expected() -> dict:
    return json.loads((WORKED / "expected.json").read_text(encoding="utf-8"))

