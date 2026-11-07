"""A long session on invented data, checked against what the generator knows. Not part of the unit tests.

Six accounts in two businesses, month after month for years: overlapping exports through different layouts and encodings,
rules, hand-booking, a statement tied and locked every month, a yearly comparison of every category with the truth,
an undone import, a killed import, a restored backup with the months after it replayed, and a salvage at the end.

    python tests/long_session.py --years 6 --rows-per-account-month 230

Exit status 0 means every figure matched. docs/TESTING.md records what it printed.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, NamedTuple, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from solo_books.cli import main  # noqa: E402
from solo_books.money import parse_cents  # noqa: E402

BIZ_A, BIZ_B = "Business A", "Business Ñandú"
INCOME_KINDS = {"4000", "4900"}


class Row(NamedTuple):
    day: date
    text: str
    cents: int
    code: str


class Account(NamedTuple):
    name: str
    business: str
    kind: str
    layout: str
    encoding: str
    opening: int


ACCOUNTS = [
    Account("A Checking", BIZ_A, "checking", "us-bank", "utf-8", 500000),
    Account("A Card", BIZ_A, "credit_card", "chase-card", "utf-8", 40000),
    Account("A Savings", BIZ_A, "savings", "", "ascii", 1000000),
    Account("B Checking", BIZ_B, "checking", "pnc", "utf-8", 200000),
    Account("B Card", BIZ_B, "credit_card", "discover-card", "cp1252", 12000),
    Account("B Other Card", BIZ_B, "credit_card", "apple-card", "utf-16", 0),
]

# Merchant, category code, typical cents, and whether it writes only Latin text.
SPEND = [
    ("BLUE FERN CAFÉ", "6150", 1800),
    ("HARBOR, PAPER SUPPLY", "6080", 6400),
    ("NORTHWIND HOSTING", "6200", 3900),
    ("SKYWAY AIR", "6140", 31000),
    ("GREEN MILL HARDWARE", "6110", 7600),
    ("HALCYON INSURANCE", "6050", 21000),
    ("ZIPNET UTILITIES", "6160", 8900),
    ("PIXEL FOUNDRY", "6030", 45000),
]
TOKYO = ("東京 STATIONERY", "6080", 2500)
ODD_JOB = "ODD JOB"
RULES = [(name.split(",")[0], code) for name, code, _ in SPEND] + [("CLIENT", "4000"), ("INTEREST PAID", "4900"), ("CARD PAYMENT", "3900"), ("PAYMENT THANK YOU", "3900"), (TOKYO[0], TOKYO[1])]


def month_starts(first_year: int, years: int) -> List[date]:
    return [date(first_year + y, m, 1) for y in range(years) for m in range(1, 13)]


def month_end(start: date) -> date:
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def invent(rng: random.Random, account: Account, start: date, count: int, month_number: int) -> List[Row]:
    """One account's rows for one month. Positive cents is money in, on every kind of account."""
    rows: List[Row] = []
    last = month_end(start)

    def when() -> date:
        return start + timedelta(days=rng.randint(0, (last - start).days))

    if account.name == "A Savings":
        rows.append(Row(last, "INTEREST PAID", rng.randint(500, 4000), "4900"))
        for _ in range(max(0, count // 20)):
            rows.append(Row(when(), f"ODD JOB CONSULT {rng.randint(1, 99)}", rng.randint(5000, 90000), "4000"))
        return sorted(rows, key=lambda r: r.day)
    for _ in range(count):
        roll = rng.random()
        if account.kind == "checking" and roll < 0.12:
            rows.append(Row(when(), f"CLIENT {rng.randint(100, 999)} DEPOSIT", rng.randint(20000, 900000), "4000"))
            continue
        if roll < 0.04:
            rows.append(Row(when(), f"{ODD_JOB} {rng.randint(1, 99)}", -rng.randint(500, 40000), "6290"))
            continue
        name, code, typical = rng.choice(SPEND)
        if account.encoding == "utf-8" and account.kind == "checking" and roll < 0.1:
            name, code, typical = TOKYO
        cents = max(1, int(rng.gauss(typical, typical / 3)))
        text = f"{name} #{rng.randint(1000, 9999)}" if rng.random() < 0.8 else name
        if rng.random() < 0.03:
            rows.append(Row(when(), text, cents, code))  # A refund.
        else:
            rows.append(Row(when(), text, -cents, code))
    if account.kind == "checking":
        rows.append(Row(last, f"NEW VENDOR {month_number} LLC", -rng.randint(1000, 90000), "6290"))
    return sorted(rows, key=lambda r: r.day)


def _plain(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(cents) // 100}.{abs(cents) % 100:02d}"


def write_export(account: Account, rows: List[Row], path: Path) -> None:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    mdy = "%m/%d/%Y"
    if account.layout == "us-bank":
        writer.writerow(["Date", "Transaction", "Name", "Memo", "Amount"])
        for r in rows:
            writer.writerow([r.day.strftime(mdy), "DEBIT" if r.cents < 0 else "CREDIT", r.text, "REF 0", _plain(r.cents)])
    elif account.layout == "chase-card":
        writer.writerow(["Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"])
        for r in rows:
            writer.writerow([r.day.strftime(mdy), r.day.strftime(mdy), r.text, "Other", "Sale", _plain(r.cents), ""])
    elif account.layout == "pnc":
        writer.writerow(["Date", "Description", "Withdrawals", "Deposits", "Balance"])
        for r in rows:
            shown = f"${abs(r.cents) // 100:,}.{abs(r.cents) % 100:02d}"
            writer.writerow([r.day.strftime(mdy), r.text, shown if r.cents < 0 else "", shown if r.cents > 0 else "", "$0.00"])
    elif account.layout == "discover-card":
        writer.writerow(["Trans. Date", "Post Date", "Description", "Amount", "Category"])
        for r in rows:
            writer.writerow([r.day.strftime(mdy), r.day.strftime(mdy), r.text, _plain(-r.cents), "Other"])
    elif account.layout == "apple-card":
        writer.writerow(["Transaction Date", "Clearing Date", "Description", "Merchant", "Category", "Type", "Amount (USD)", "Purchased By"])
        for r in rows:
            writer.writerow([r.day.strftime(mdy), r.day.strftime(mdy), r.text, r.text, "Other", "Purchase", _plain(-r.cents), "A Example"])
    else:
        out.write("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n\n<OFX><BANKMSGSRSV1><STMTRS><CURDEF>USD<BANKTRANLIST>\n")
        for r in rows:
            fitid = f"{r.day:%Y%m%d}{abs(hash_text(r.text)) % 100000}{abs(r.cents)}"
            out.write(f"<STMTTRN>\n<DTPOSTED>{r.day:%Y%m%d}\n<TRNAMT>{_plain(r.cents)}\n<FITID>{fitid}\n<NAME>{r.text}\n</STMTTRN>\n")
        out.write("</BANKTRANLIST></STMTRS></BANKMSGSRSV1></OFX>\n")
    path.write_bytes(out.getvalue().encode(account.encoding))


def hash_text(text: str) -> int:
    return sum((i + 1) * ord(ch) for i, ch in enumerate(text))


class Session:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.db = work / "books.sqlite3"
        self.times: Dict[str, List[float]] = defaultdict(list)
        self.mismatches: List[str] = []
        self.counts = Counter()

    def run(self, *args: str, expect: int = 0) -> str:
        out, err = io.StringIO(), io.StringIO()
        start = time.perf_counter()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(["--db", str(self.db), *args])
            except SystemExit as exc:
                code = exc.code
        label = " ".join(a for a in args[:2] if not a[0].isupper() and not a.startswith("-") and "/" not in a and "\\" not in a)
        self.times[label].append(time.perf_counter() - start)
        self.counts["commands"] += 1
        if code != expect:
            self.mismatches.append(f"{args}: exit {code}, wanted {expect}: {err.getvalue()[:400]} {out.getvalue()[-400:]}")
        return out.getvalue()

    def compare(self, what: str, got: int, wanted: int) -> None:
        self.counts["figures compared"] += 1
        if got != wanted:
            self.mismatches.append(f"{what}: books say {got}, truth is {wanted}")


def setup(session: Session, first_day: date) -> None:
    session.run("init")
    for business in (BIZ_A, BIZ_B):
        session.run("business", "add", business)
        for pattern, code in RULES:
            session.run("rules", "add", "--business", business, "--contains", pattern, "--category", code)
    for account in ACCOUNTS:
        args = ["account", "add", account.name, "--business", account.business, "--kind", account.kind]
        args += ["--opening-balance", _plain(account.opening), "--opening-date", first_day.isoformat()]
        if account.layout:
            args += ["--layout", account.layout]
        session.run(*args)


def play_month(session: Session, rows_by_account: Dict[str, List[Row]], start: date, month_number: int, truth_balance: Dict[str, int]) -> None:
    last = month_end(start)
    overlap_from = start - timedelta(days=10)
    for account in ACCOUNTS:
        rows = [r for r in rows_by_account[account.name] if overlap_from <= r.day <= last]
        path = session.work / f"export{'.qfx' if not account.layout else '.csv'}"
        write_export(account, rows, path)
        session.run("import", account.name, str(path))
        session.counts["imports"] += 1
    for business in (BIZ_A, BIZ_B):
        session.run("rules", "add", "--business", business, "--contains", f"NEW VENDOR {month_number} ", "--category", "6290")
        # What the rules could not book, booked by hand, the way a person would from the queue.
        odd = session.run("txn", "list", "--business", business, "--search", ODD_JOB, "--uncategorized", "--format", "csv")
        for row in csv.DictReader(io.StringIO(odd)):
            if not row.get("transaction_id"):
                continue
            code = "4000" if parse_cents(row["amount"]) > 0 else "6290"
            session.run("categorize", code, row["transaction_id"])
            session.counts["booked by hand"] += 1
    for account in ACCOUNTS:
        moved = sum(r.cents for r in rows_by_account[account.name] if start <= r.day <= last)
        begin = truth_balance[account.name]
        end = begin + moved
        truth_balance[account.name] = end
        shown_begin, shown_end = (-begin, -end) if account.kind == "credit_card" else (begin, end)
        out = session.run("reconcile", "run", account.name, "--from", start.isoformat(), "--to", last.isoformat(), "--begin", _plain(shown_begin), "--end", _plain(shown_end))
        session.counts["statements tied"] += 1
        recon = out.split("Reconciliation ", 1)[1].split(":", 1)[0]
        session.run("reconcile", "finish", recon)


def compare_year(session: Session, rows_by_account: Dict[str, List[Row]], year: int) -> None:
    for business in (BIZ_A, BIZ_B):
        wanted: Dict[str, int] = defaultdict(int)
        rows_total = 0
        for account in ACCOUNTS:
            if account.business != business:
                continue
            for r in rows_by_account[account.name]:
                if r.day.year == year:
                    sign = 1 if r.code in INCOME_KINDS else -1
                    wanted[r.code] += sign * r.cents
                    rows_total += r.cents
        text = session.run("report", "pnl", "--period", str(year), "--business", business, "--format", "csv")
        got = {row["code"]: parse_cents(row["amount"]) for row in csv.DictReader(io.StringIO(text)) if row.get("code") and row["code"][0] in "456"}
        for code, cents in wanted.items():
            session.compare(f"{business} {year} {code}", got.get(code, 0), cents)
        session.run("report", "schedule-c", "--year", str(year), "--lines-year", "2025", "--business", business)
        session.run("report", "balances", "--period", str(year), "--business", business)
    folder = session.work / f"preparer-{year}"
    session.run("report", "preparer", "--year", str(year), "--lines-year", "2025", "--out", str(folder))
    for business in (BIZ_A, BIZ_B):
        stem = "business-a" if business == BIZ_A else "business-ñandú"
        detail = folder / f"{stem}-transactions-{year}.csv"
        with open(detail, encoding="utf-8-sig", newline="") as handle:
            total = sum(parse_cents(row["amount"]) for row in csv.DictReader(handle) if row.get("date") and row.get("amount"))
        wanted_total = sum(r.cents for a in ACCOUNTS if a.business == business for r in rows_by_account[a.name] if r.day.year == year)
        session.compare(f"{business} {year} preparer rows", total, wanted_total)


def killed_import(session: Session, account: Account, rows: List[Row]) -> None:
    """Kill a real process as its write begins, then check the books and carry on."""
    path = session.work / "killed.csv"
    write_export(account, rows, path)
    env = dict(os.environ, PYTHONPATH=str(REPO))
    process = subprocess.Popen(
        [sys.executable, "-m", "solo_books", "--db", str(session.db), "import", account.name, str(path)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    journal = Path(str(session.db) + "-journal")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and process.poll() is None and not journal.exists():
        time.sleep(0.001)
    process.kill()
    process.wait()
    session.run("check")
    session.counts["killed imports"] += 1


def main_session() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--rows-per-account-month", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--first-year", type=int, default=2020)
    options = parser.parse_args()
    rng = random.Random(options.seed)
    work = Path(tempfile.mkdtemp(prefix="solo-books-long-"))
    session = Session(work)
    began = time.perf_counter()
    months = month_starts(options.first_year, options.years)
    rows_by_account: Dict[str, List[Row]] = {a.name: [] for a in ACCOUNTS}
    for number, start in enumerate(months):
        for account in ACCOUNTS:
            rows_by_account[account.name] += invent(rng, account, start, options.rows_per_account_month, number)
    total_rows = sum(len(v) for v in rows_by_account.values())
    setup(session, months[0])
    balance = {a.name: (-a.opening if a.kind == "credit_card" else a.opening) for a in ACCOUNTS}
    restore_at = min(len(months) - 1, 14)
    backup_at, backup_balance, backup_file, restored = None, None, work / "kept-backup.sqlite3", False
    index = 0
    while index < len(months):
        start = months[index]
        if index == 5:
            # An import taken back and done again changes nothing.
            account = ACCOUNTS[0]
            path = work / "again.csv"
            write_export(account, [r for r in rows_by_account[account.name] if r.day.year == start.year and r.day.month == start.month], path)
            session.run("import", account.name, str(path))
            session.run("imports", "undo", session.run("imports", "list").strip().splitlines()[-1].split()[0])
            session.counts["undone imports"] += 1
        if index == 8:
            account = ACCOUNTS[3]
            killed_import(session, account, [r for r in rows_by_account[account.name] if start <= r.day <= month_end(start)])
        if index == restore_at - 3 and not restored:
            session.run("backup", str(backup_file))
            backup_at, backup_balance = index, dict(balance)
        play_month(session, rows_by_account, start, index, balance)
        if index == restore_at and not restored:
            session.run("restore", str(backup_file))
            restored = True
            session.counts["restores"] += 1
            index, balance = backup_at, dict(backup_balance)
            continue
        if start.month == 12:
            compare_year(session, rows_by_account, start.year)
            session.run("check")
        index += 1
    report = session.run("check")
    session.counts["books rows"] = int(report.split(" row(s),")[0].rsplit(" ", 1)[-1])
    session.compare("rows in the books", session.counts["books rows"], total_rows)
    session.run("status")
    salvaged = work / "salvaged.sqlite3"
    session.run("salvage", str(salvaged))
    session.db = salvaged
    session.run("check")
    size = (work / "books.sqlite3").stat().st_size
    took = time.perf_counter() - began
    print(f"Invented rows: {total_rows} over {options.years} years in {len(ACCOUNTS)} accounts. Books file: {size / 1e6:.1f} MB. Total time: {took:.0f} s.")
    print(f"Python {sys.version.split()[0]} on {sys.platform}.")
    for name, value in sorted(session.counts.items()):
        print(f"  {name}: {value}")
    print("  command: runs, mean seconds, slowest seconds")
    for label, times in sorted(session.times.items(), key=lambda item: -sum(item[1]))[:14]:
        print(f"    {label}: {len(times)}, {sum(times) / len(times):.3f}, {max(times):.3f}")
    shutil.rmtree(work, ignore_errors=True)
    if session.mismatches:
        print(f"MISMATCHES: {len(session.mismatches)}")
        for line in session.mismatches[:25]:
            print(" ", line[:600])
        return 1
    print("Every figure matched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_session())
