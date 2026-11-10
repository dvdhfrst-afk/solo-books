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


SAMPLES = REPO_ROOT / "samples"


def build_worked_books(case: BooksCase, review: bool = True) -> None:
    """The hand-checked example in tests/fixtures/worked, imported as WORKED.md describes it."""
    case.ok("business", "add", "Business A", "--activity", "Consulting")
    case.ok("business", "add", "Business B", "--activity", "Online shop")
    case.ok(
        "account", "add", "A Checking", "--business", "Business A", "--kind", "checking",
        "--institution", "Example Bank", "--opening-balance", "5000.00", "--opening-date", "2025-01-01",
        "--date-col", "Date", "--description-col", "Description", "--amount-col", "Amount",
    )
    case.ok(
        "account", "add", "A Card", "--business", "Business A", "--kind", "credit_card",
        "--institution", "Example Card", "--opening-balance", "200.00", "--opening-date", "2025-01-01",
        "--date-col", "Post Date", "--description-col", "Description", "--amount-col", "Amount",
    )
    case.ok(
        "account", "add", "B Checking", "--business", "Business B", "--kind", "checking",
        "--opening-balance", "1000.00", "--opening-date", "2025-01-01", "--date-col", "Date",
        "--description-col", "Description", "--debit-col", "Debit", "--credit-col", "Credit",
        "--status-col", "Status", "--skip-lines", "2",
    )
    case.ok("rules", "import", str(WORKED / "rules_a.csv"), "--business", "Business A")
    case.ok("rules", "import", str(WORKED / "rules_b.csv"), "--business", "Business B")
    case.ok("import", "A Checking", str(WORKED / "a_checking_jan_feb.csv"))
    case.ok("import", "A Checking", str(WORKED / "a_checking_feb_mar.csv"))
    case.ok("import", "A Card", str(WORKED / "a_card_q1.csv"))
    case.ok("import", "B Checking", str(WORKED / "b_checking_q1.csv"))
    if review:
        # The rules leave the card payments, the draw and the cafe for a person to decide, as WORKED.md does.
        decide = {"EXAMPLE CARD PAYMENT": "Transfer", "PAYMENT THANK YOU": "Transfer", "OWNER DRAW TO PERSONAL": "3100"}
        queue = case.conn.execute("SELECT id, description FROM transactions WHERE category_id IS NULL ORDER BY id").fetchall()
        for row in queue:
            case.ok("categorize", decide.get(row["description"], "6150"), str(row["id"]))
    case.ok("inventory", "set", "--business", "Business B", "--year", "2025", "--beginning", "500.00", "--ending", "750.00")


def build_sample_books(case: BooksCase, review: bool = True) -> None:
    """The README's walk through the sample year. With review, the queue is cleared and the missing row added."""
    case.ok("business", "add", "Business A", "--activity", "Consulting")
    case.ok("business", "add", "Business B", "--activity", "Online shop")
    for name, business, kind, opening, mapping in (
        ("A Checking", "Business A", "checking", "12500.00", "a-checking"),
        ("A Card", "Business A", "credit_card", "425.00", "a-card"),
        ("B Checking", "Business B", "checking", "3000.00", "b-checking"),
    ):
        case.ok(
            "account", "add", name, "--business", business, "--kind", kind, "--opening-balance", opening,
            "--opening-date", "2025-01-01", "--mapping", str(SAMPLES / "mappings" / f"{mapping}.json"),
        )
    case.ok("rules", "import", str(SAMPLES / "rules-business-a.csv"), "--business", "Business A")
    case.ok("rules", "import", str(SAMPLES / "rules-business-b.csv"), "--business", "Business B")
    case.ok("import", "A Checking", str(SAMPLES / "example-bank-a-checking-2025-01-to-07.csv"))
    case.ok("import", "A Checking", str(SAMPLES / "example-bank-a-checking-2025-06-to-12.csv"))
    case.ok("import", "A Card", str(SAMPLES / "example-card-a-2025.csv"))
    case.ok("import", "B Checking", str(SAMPLES / "example-bank-b-checking-2025.csv"))
    case.ok("rules", "add", "--business", "Business A", "--contains", "CARD PAYMENT", "--category", "Transfer")
    case.ok("rules", "add", "--business", "Business A", "--contains", "PAYMENT THANK YOU", "--category", "Transfer")
    case.ok("rules", "add", "--business", "Business A", "--contains", "OWNER DRAW", "--category", "Owner draws")
    case.ok("rules", "add", "--business", "Business B", "--contains", "OWNER DRAW", "--category", "Owner draws")
    if review:
        case.ok("review", stdin="r 6150\nr 6110\n")
        case.ok(
            "txn", "add", "A Card", "--date", "2025-03-27", "--amount", "-15.00",
            "--description", "CITY PARKING GARAGE", "--category", "Travel",
        )
        case.ok("inventory", "set", "--business", "Business B", "--year", "2025", "--beginning", "1500.00", "--ending", "2200.00")


def sample_expected() -> dict:
    return json.loads((SAMPLES / "expected-2025.json").read_text(encoding="utf-8"))

