"""Journal exports, read back by small readers written here for each format. No outside accounting program is used.

Every transaction must add to zero, every account must end where the books say it does, and income less expenses
must tie to profit and loss.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date
from typing import Dict, List, Tuple

from solo_books import books, ledger
from tests.helpers import BooksCase, build_worked_books, worked_expected

LEDGER_HEADER = re.compile(r"^(\d{4})/(\d{2})/(\d{2}) (?:([*!]) )?(?:\((\d+)\) )?(.+)$")
LEDGER_POSTING = re.compile(r"^    (\S(?:\S| (?! ))*)  +\$(-?\d+)\.(\d{2})$")
BEANCOUNT_HEADER = re.compile(r'^(\d{4}-\d{2}-\d{2}) ([*!]) "((?:[^"\\]|\\.)*)"$')
BEANCOUNT_POSTING = re.compile(r"^  ([A-Z][A-Za-z0-9-]*(?::[A-Z0-9][A-Za-z0-9-]*)+) +(-?\d+)\.(\d{2}) USD$")
BEANCOUNT_META = re.compile(r'^  ([a-z][a-z_]*): (.+)$')


def _cents(whole: str, fraction: str) -> int:
    value = abs(int(whole)) * 100 + int(fraction)
    return -value if whole.startswith("-") else value


def read_ledger(text: str) -> List[dict]:
    """Transactions with their postings. Anything this program would never write is an error."""
    transactions: List[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("; ") or line.startswith("account "):
            continue
        header = LEDGER_HEADER.match(line)
        if header:
            year, month, day, flag, code, payee = header.groups()
            transactions.append({
                "date": date(int(year), int(month), int(day)), "flag": flag, "id": int(code) if code else None,
                "payee": payee, "postings": [], "comments": [],
            })
            continue
        if line.startswith("    ; "):
            transactions[-1]["comments"].append(line[6:])
            continue
        posting = LEDGER_POSTING.match(line)
        if not posting:
            raise AssertionError(f"line {number} is not Ledger this program writes: {line!r}")
        transactions[-1]["postings"].append((posting.group(1), _cents(posting.group(2), posting.group(3))))
    return transactions


def read_beancount(text: str) -> Tuple[List[dict], Dict[str, str], List[Tuple[str, str, int]]]:
    """Transactions, the open date of each account, and the balance checks."""
    transactions: List[dict] = []
    opened: Dict[str, str] = {}
    balances: List[Tuple[str, str, int]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("; "):
            continue
        parts = line.split()
        if len(parts) == 4 and parts[1] == "open" and parts[3] == "USD":
            opened[parts[2]] = parts[0]
            continue
        if len(parts) == 5 and parts[1] == "balance" and parts[4] == "USD":
            whole, fraction = parts[3].split(".")
            balances.append((parts[0], parts[2], _cents(whole, fraction)))
            continue
        header = BEANCOUNT_HEADER.match(line)
        if header:
            narration = re.sub(r"\\(.)", r"\1", header.group(3))
            transactions.append({"date": header.group(1), "flag": header.group(2), "narration": narration, "postings": [], "meta": {}})
            continue
        meta = BEANCOUNT_META.match(line)
        if meta and transactions and not transactions[-1]["postings"]:
            transactions[-1]["meta"][meta.group(1)] = meta.group(2)
            continue
        posting = BEANCOUNT_POSTING.match(line)
        if not posting:
            raise AssertionError(f"line {number} is not Beancount this program writes: {line!r}")
        transactions[-1]["postings"].append((posting.group(1), _cents(posting.group(2), posting.group(3))))
    return transactions, opened, balances


def read_qif(text: str) -> Dict[str, List[dict]]:
    """Records per account. Each record is the fields it carries, by their letter."""
    accounts: Dict[str, List[dict]] = {}
    section, current, record = "", None, {}
    pending_account = None
    for line in text.splitlines():
        if line.startswith("!"):
            section = line
            if section.startswith("!Type:") and section != "!Type:Cat":
                current = pending_account
                accounts[current] = []
            continue
        if line == "^":
            if section == "!Account":
                pending_account = record["N"]
            elif section not in ("!Type:Cat",):
                accounts[current].append(record)
            record = {}
            continue
        if line[:1] in ("I", "E") and len(line) == 1:
            record["kind"] = line
            continue
        record[line[0]] = line[1:]
    return accounts


def by_account(transactions: List[dict]) -> Dict[str, int]:
    totals: Dict[str, int] = defaultdict(int)
    for txn in transactions:
        for account, cents in txn["postings"]:
            totals[account] += cents
    return dict(totals)


class JournalCase(BooksCase):
    def export(self, fmt: str, *extra: str) -> str:
        path = self.dir / f"journal.{fmt}"
        out = self.ok("export", "journal", "--format", fmt, "--period", "2025-Q1", "-o", str(path), *extra)
        self.assertIn("not tax or accounting advice", out)
        return path.read_text(encoding="utf-8-sig")


class WorkedJournals(JournalCase):
    def setUp(self) -> None:
        super().setUp()
        build_worked_books(self)
        self.expected = worked_expected()
        a, b = self.expected["business_a"]["pnl"], self.expected["business_b"]["pnl"]
        self.net = {"Business A": int(a["net"].replace(".", "")), "Business B": int(b["net"].replace(".", ""))}

    def check_ties(self, totals: Dict[str, int], sep: str, business: Dict[str, str]) -> None:
        for name, part in business.items():
            income = sum(v for k, v in totals.items() if k.startswith(f"Income{sep}{part}{sep}"))
            expenses = sum(v for k, v in totals.items() if k.startswith(f"Expenses{sep}{part}{sep}"))
            self.assertEqual(-(income + expenses), self.net[name], name)

    def test_ledger_balances_and_ties_to_profit_and_loss(self) -> None:
        text = self.export("ledger")
        self.assertTrue(text.startswith("; Written by solo-books "))
        self.assertIn("; Bookkeeping summary, not tax or accounting advice.", text)
        transactions = read_ledger(text)
        for txn in transactions:
            self.assertEqual(len(txn["postings"]), 2, txn)
            self.assertEqual(sum(cents for _, cents in txn["postings"]), 0, txn)
        rows = [t for t in transactions if t["id"] is not None]
        self.assertEqual(len(rows), self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
        totals = by_account(transactions)
        self.assertEqual(totals["Assets:Business A:A Checking"], 1192815)
        self.assertEqual(totals["Liabilities:Business A:A Card"], -51062)
        self.assertEqual(totals["Assets:Business B:B Checking"], 573225)
        self.assertEqual(totals["Assets:Business A:Transfer clearing"], 0)
        self.assertEqual(totals["Equity:Business A:Owner draws"], 150000)
        self.assertEqual(totals["Expenses:Business A:Travel"], 53000)
        self.assertEqual(totals["Income:Business B:Returns and allowances"], 4500)
        self.assertEqual(totals["Equity:Business A:Opening balances"], -480000)
        self.check_ties(totals, ":", {"Business A": "Business A", "Business B": "Business B"})
        self.assertEqual([t["date"] for t in transactions], sorted(t["date"] for t in transactions))
        self.assertEqual(transactions[0]["payee"], "Opening balance")

    def test_beancount_balances_opens_and_checks(self) -> None:
        transactions, opened, balances = read_beancount(self.export("beancount"))
        for txn in transactions:
            self.assertEqual(sum(cents for _, cents in txn["postings"]), 0, txn)
            for account, _ in txn["postings"]:
                self.assertLessEqual(opened[account], txn["date"], account)
        totals = by_account(transactions)
        for day, account, cents in balances:
            self.assertEqual(day, "2025-04-01")
            self.assertEqual(totals[account], cents, account)
        self.assertEqual(
            {account: cents for _, account, cents in balances},
            {"Assets:Business-A:A-Checking": 1192815, "Liabilities:Business-A:A-Card": -51062, "Assets:Business-B:B-Checking": 573225},
        )
        self.check_ties(totals, ":", {"Business A": "Business-A", "Business B": "Business-B"})
        ids = [int(t["meta"]["solo_books_id"]) for t in transactions if "solo_books_id" in t["meta"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(t["flag"] == "*" for t in transactions))

    def test_qif_records_tie_to_each_account_and_category(self) -> None:
        accounts = read_qif(self.export("qif"))
        self.assertEqual(set(accounts), {"A Checking", "A Card", "B Checking"})
        ends = {"A Checking": 1192815, "A Card": -51062, "B Checking": 573225}
        categories: Dict[str, int] = defaultdict(int)
        for name, records in accounts.items():
            self.assertEqual(sum(_cents(*r["T"].split(".")) for r in records), ends[name], name)
            self.assertEqual(records[0]["P"], "Opening balance")
            self.assertEqual(records[0]["L"], f"[{name}]")
            for record in records:
                self.assertRegex(record["D"], r"^\d{2}/\d{2}/2025$")
                categories[record["L"]] += _cents(*record["T"].split("."))
        self.assertEqual(categories["Business A:Travel"], -53000)
        self.assertEqual(categories["[Business A Transfer clearing]"], 0)
        net_a = sum(v for k, v in categories.items() if k.startswith("Business A:") and k != "Business A:Owner draws")
        self.assertEqual(net_a, self.net["Business A"])
        self.assertIn("not tax or accounting advice", self.export("qif"))

    def test_csv_postings_pair_up_to_zero(self) -> None:
        rows = list(csv.DictReader(io.StringIO(self.export("csv"))))
        entries: Dict[str, int] = defaultdict(int)
        totals: Dict[str, int] = defaultdict(int)
        for row in rows:
            if row["status"] == "note":
                continue
            entries[row["entry"]] += _cents(*row["amount"].split("."))
            totals[row["account"]] += _cents(*row["amount"].split("."))
        self.assertTrue(all(v == 0 for v in entries.values()))
        self.check_ties(totals, ":", {"Business A": "Business A", "Business B": "Business B"})
        self.assertEqual(rows[-3]["description"], "Bookkeeping summary, not tax or accounting advice.")

    def test_one_business_and_one_month(self) -> None:
        path = self.dir / "feb.ledger"
        self.ok("export", "journal", "--format", "ledger", "--period", "2025-02", "--business", "Business A", "-o", str(path))
        transactions = read_ledger(path.read_text(encoding="utf-8"))
        totals = by_account(transactions)
        self.assertNotIn("Assets:Business B:B Checking", totals)
        # February starts from January's ending balances, as WORKED.md works them out.
        openings = {t["postings"][0][0]: t["postings"][0][1] for t in transactions if t["id"] is None}
        self.assertEqual(openings, {"Assets:Business A:A Checking": 818800, "Liabilities:Business A:A Card": -45970})
        self.assertEqual(totals["Assets:Business A:A Checking"], 1035125)
        self.assertEqual(totals["Liabilities:Business A:A Card"], -42709)
        net = -sum(v for k, v in totals.items() if k.startswith(("Income:", "Expenses:")))
        self.assertEqual(net, int(self.expected["business_a"]["pnl_by_month"]["2025-02"]["net"].replace(".", "")))

    def test_rows_waiting_for_review_and_reconciled_rows_are_marked(self) -> None:
        acct = books.get_account(self.conn, "A Checking")
        ledger.add_manual(self.conn, acct, date(2025, 3, 30), -999, "UNKNOWN PAYEE")
        self.ok("reconcile", "run", "A Checking", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "5000.00", "--end", "8188.00")
        self.ok("reconcile", "finish", "1")
        path = self.dir / "q1.ledger"
        out = self.ok("export", "journal", "--format", "ledger", "--period", "2025-Q1", "-o", str(path))
        self.assertIn("WARNING: 1 row(s) waiting for review", out)
        transactions = read_ledger(path.read_text(encoding="utf-8"))
        waiting = [t for t in transactions if t["payee"] == "UNKNOWN PAYEE"][0]
        self.assertEqual(waiting["flag"], "!")
        self.assertEqual(waiting["postings"][1], ("Equity:Business A:Uncategorized", 999))
        january = [t for t in transactions if t["id"] is not None and t["date"].month == 1 and t["postings"][0][0] == "Assets:Business A:A Checking"]
        self.assertTrue(january and all(t["flag"] == "*" for t in january))
        later = [t for t in transactions if t["id"] is not None and t["date"].month == 2 and t["postings"][0][0] == "Assets:Business A:A Checking"]
        self.assertTrue(later and all(t["flag"] is None for t in later))
        self.check_ties(by_account(transactions), ":", {"Business A": "Business A", "Business B": "Business B"})
        beancount, _, _ = read_beancount(self.export("beancount"))
        self.assertEqual([t["flag"] for t in beancount if t["narration"] == "UNKNOWN PAYEE"], ["!"])


class JournalText(JournalCase):
    """Text from a bank must not change how a journal line reads, in any format."""

    def setUp(self) -> None:
        super().setUp()
        self.biz = books.add_business(self.conn, "Shop: North; Ltd")
        self.acct = books.add_account(self.conn, self.biz, "Till [main]", "checking", "Example Bank", 10000, date(2025, 1, 1))
        books.add_category(self.conn, self.biz["id"], "6400", "Meals & drinks", "expense")
        books.add_category(self.conn, self.biz["id"], "6401", "Meals, drinks", "expense")
        self.descriptions = [
            'CAFE "NORTH" \\ BAR ; tip  two  spaces',
            "(VOID) * ! starts like a flag",
            "=CMD|' /C calc'!A0",
        ]
        for number, (text, code) in enumerate(zip(self.descriptions, ("6400", "6401", "6400")), start=1):
            category = books.find_category(self.conn, self.biz["id"], code)
            ledger.add_manual(self.conn, self.acct, date(2025, 1, number), -100 * number, text, category, note="a; note")

    def test_ledger_keeps_each_line_whole(self) -> None:
        transactions = read_ledger(self.export("ledger"))
        payees = [t["payee"] for t in transactions if t["id"] is not None]
        self.assertEqual(payees[0], 'CAFE "NORTH" \\ BAR , tip two spaces')
        self.assertEqual(payees[1], "(VOID) * ! starts like a flag")
        accounts = {a for t in transactions for a, _ in t["postings"]}
        self.assertIn("Assets:Shop North Ltd:Till main", accounts)
        meals = sorted(a for a in accounts if a.startswith("Expenses:"))
        self.assertEqual(len(meals), 2)
        for txn in transactions:
            self.assertEqual(sum(c for _, c in txn["postings"]), 0)
        self.assertIn("Note: a; note", [c for t in transactions for c in t["comments"]])

    def test_beancount_quotes_and_names(self) -> None:
        transactions, opened, _ = read_beancount(self.export("beancount"))
        narrations = [t["narration"] for t in transactions if "solo_books_id" in t["meta"]]
        self.assertEqual(narrations, [" ".join(d.split()) for d in self.descriptions])
        for account in opened:
            self.assertRegex(account, r"^(Assets|Liabilities|Equity|Income|Expenses)(:[A-Z0-9][A-Za-z0-9-]*)+$")
        self.assertIn("Assets:Shop-North-Ltd:Till-main", opened)
        meals = sorted(a for a in opened if a.startswith("Expenses:"))
        self.assertEqual(meals, ["Expenses:Shop-North-Ltd:Meals-drinks", "Expenses:Shop-North-Ltd:Meals-drinks-6401"])

    def test_qif_fields_stay_on_one_line(self) -> None:
        text = self.export("qif")
        accounts = read_qif(text)
        records = accounts["Till -main-"]
        self.assertEqual([r["P"] for r in records[1:]], [" ".join(d.split()) for d in self.descriptions])
        self.assertEqual(records[1]["L"], "Shop- North; Ltd:Meals & drinks")
        for line in text.splitlines():
            self.assertTrue(line[:1] in "!DTPMLNC^IE", line)

    def test_csv_neutralizes_formulas(self) -> None:
        rows = list(csv.DictReader(io.StringIO(self.export("csv"))))
        self.assertIn("'=CMD|' /C calc'!A0", [r["description"] for r in rows])
        for row in rows:
            for cell in row.values():
                self.assertFalse(cell[:1] in ("=", "+", "@") and not re.match(r"^[-+]?\d", cell), cell)


class AccountsOpenedLater(JournalCase):
    def test_an_account_opened_inside_the_period_starts_on_its_opening_day(self) -> None:
        biz = self.business()
        acct = books.add_account(self.conn, biz, "A Checking", "checking", "Example Bank", 50000, date(2025, 2, 10))
        sales = self.category(biz, "4000")
        # A row dated before the opening date is still in profit and loss, so the journal keeps it too.
        ledger.add_manual(self.conn, acct, date(2025, 2, 1), 2500, "EARLY SALE", sales)
        ledger.add_manual(self.conn, acct, date(2025, 3, 1), 7500, "SALE", sales)
        transactions = read_ledger(self.export("ledger"))
        opening = [t for t in transactions if t["id"] is None][0]
        self.assertEqual(opening["date"], date(2025, 2, 10))
        self.assertEqual(opening["postings"][0], ("Assets:Business A:A Checking", 50000 - 2500))
        totals = by_account(transactions)
        self.assertEqual(totals["Assets:Business A:A Checking"], ledger.balance_through(self.conn, acct, date(2025, 3, 31)))
        self.assertEqual(totals["Income:Business A:Sales"], -10000)

    def test_an_account_with_no_opening_balance_has_no_opening_entry(self) -> None:
        biz = self.business()
        acct = self.account(biz)
        ledger.add_manual(self.conn, acct, date(2025, 1, 2), -500, "FEE", self.category(biz, "6210"))
        transactions, _, balances = read_beancount(self.export("beancount"))
        self.assertEqual(len(transactions), 1)
        self.assertEqual(balances, [])

    def test_a_bad_period_or_format_is_refused(self) -> None:
        self.business()
        self.assertIn("not a period", self.fails("export", "journal", "--format", "ledger", "--period", "soon", "-o", str(self.dir / "x")))
        code, _, _ = self.run_cli("export", "journal", "--format", "gnucash", "--period", "2025", "-o", str(self.dir / "x"))
        self.assertEqual(code, 2)
