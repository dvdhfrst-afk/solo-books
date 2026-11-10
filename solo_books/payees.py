"""The payee directory: who money went to and came from, built from the merchant names rows already carry.

Every row has a merchant name worked out from its description, such as PIXEL FOUNDRY CONTRACTOR. Each name is a payee
of its own until a person says otherwise: a better name to show, a contractor flag, or that it is another spelling of a
second payee. Merging keeps both spellings' rows together under the second payee; unmerging takes a spelling back out.
Nothing here stores a tax identification number or an address.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import history
from .db import transaction
from .errors import BooksError
from .ledger import tag_filter
from .periods import Period
from .text import clean_cell, normalize_description

MAX_PAYEE_NAME = 120
# Merges point at the payee they joined. A chain longer than this means the file was changed by something else.
_MAX_CHAIN = 50
OFF_PAYEE_KINDS = ("transfer", "equity")


@dataclass
class Payee:
    """One payee: the spelling that holds its settings, the name shown, and every spelling that belongs to it."""

    key: str
    name: str
    contractor: bool
    record_id: Optional[int]
    merchants: List[str] = field(default_factory=list)

    @property
    def renamed(self) -> bool:
        return self.name != self.key


def _records(conn: sqlite3.Connection, business_id: int) -> Dict[int, sqlite3.Row]:
    return {row["id"]: row for row in conn.execute("SELECT * FROM payees WHERE business_id = ?", (business_id,))}


def _root(records: Dict[int, sqlite3.Row], record_id: int) -> sqlite3.Row:
    record = records[record_id]
    for _ in range(_MAX_CHAIN):
        parent = record["merged_into"]
        if parent is None or parent not in records:
            return record
        record = records[parent]
    raise BooksError("the payee merges in these books go round in a circle. Run 'check'.")


def directory(conn: sqlite3.Connection, business_id: int) -> Dict[str, Payee]:
    """Every merchant spelling this business has, from its rows or from what a person said, and the payee it is."""
    records = _records(conn, business_id)
    by_merchant = {row["merchant"]: row for row in records.values()}
    spellings = set(by_merchant)
    spellings.update(
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT t.merchant FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE a.business_id = ?",
            (business_id,),
        )
    )
    payees: Dict[str, Payee] = {}
    found: Dict[str, Payee] = {}
    for merchant in sorted(spellings):
        record = by_merchant.get(merchant)
        root = _root(records, record["id"]) if record is not None else None
        key = root["merchant"] if root is not None else merchant
        payee = payees.get(key)
        if payee is None:
            payee = Payee(
                key=key,
                name=(root["display_name"] if root is not None else None) or key,
                contractor=bool(root["contractor"]) if root is not None else False,
                record_id=root["id"] if root is not None else None,
            )
            payees[key] = payee
        payee.merchants.append(merchant)
        found[merchant] = payee
    return found


def all_payees(conn: sqlite3.Connection, business_id: int) -> List[Payee]:
    unique: Dict[str, Payee] = {}
    for payee in directory(conn, business_id).values():
        unique[payee.key] = payee
    return sorted(unique.values(), key=lambda payee: payee.name.lower())


def find_payee(conn: sqlite3.Connection, business_id: int, text: str) -> Payee:
    """By a merchant spelling exactly as rows carry it, or else by the name a payee is shown under."""
    found = directory(conn, business_id)
    spelling = normalize_description(text)
    if spelling in found:
        return found[spelling]
    wanted = " ".join(text.split()).lower()
    named = {payee.key: payee for payee in found.values() if payee.name.lower() == wanted}
    if len(named) == 1:
        return next(iter(named.values()))
    if len(named) > 1:
        raise BooksError(f"{text!r} names more than one payee. Use a merchant spelling from 'payee list'.")
    raise BooksError(f"no payee or merchant spelling {text!r} in this business. See 'payee list'.")


def _ensure_record(conn: sqlite3.Connection, business_id: int, merchant: str) -> sqlite3.Row:
    conn.execute("INSERT OR IGNORE INTO payees (business_id, merchant) VALUES (?, ?)", (business_id, merchant))
    return conn.execute("SELECT * FROM payees WHERE business_id = ? AND merchant = ?", (business_id, merchant)).fetchone()


def _clean_name(name: str) -> str:
    text = clean_cell(name)
    if not text:
        raise BooksError("a payee needs a name")
    if len(text) > MAX_PAYEE_NAME:
        raise BooksError(f"a payee's name can be at most {MAX_PAYEE_NAME} characters")
    return text


def rename_payee(conn: sqlite3.Connection, business_id: int, text: str, new_name: str) -> Tuple[Payee, str]:
    """Change the name a payee is shown under. Its rows keep the merchant names the bank gave them."""
    payee = find_payee(conn, business_id, text)
    name = _clean_name(new_name)
    for other in all_payees(conn, business_id):
        if other.key != payee.key and other.name.lower() == name.lower():
            raise BooksError(
                f"{other.name!r} is already a payee. If they are one payee, merge them: payee merge {text!r} {other.name!r}"
            )
    with transaction(conn):
        record = _ensure_record(conn, business_id, payee.key)
        conn.execute("UPDATE payees SET display_name = ? WHERE id = ?", (None if name == payee.key else name, record["id"]))
        history.record(
            conn, "payee-rename", f"Payee {payee.name} is now shown as {name}.", business_id,
            {"merchant": payee.key, "before": payee.name, "after": name},
        )
    return payee, name


def merge_payees(conn: sqlite3.Connection, business_id: int, text: str, into_text: str) -> Dict:
    """Make the first payee another spelling of the second. The second keeps its name; a contractor flag carries over."""
    joining = find_payee(conn, business_id, text)
    keeping = find_payee(conn, business_id, into_text)
    if joining.key == keeping.key:
        raise BooksError(f"{text!r} and {into_text!r} are already one payee, {keeping.name}.")
    carried = joining.contractor and not keeping.contractor
    with transaction(conn):
        target = _ensure_record(conn, business_id, keeping.key)
        moving = _ensure_record(conn, business_id, joining.key)
        conn.execute("UPDATE payees SET merged_into = ? WHERE id = ?", (target["id"], moving["id"]))
        if carried:
            conn.execute("UPDATE payees SET contractor = 1 WHERE id = ?", (target["id"],))
        history.record(
            conn, "payee-merge", f"{joining.name} is now part of payee {keeping.name}.", business_id,
            {
                "from": joining.key, "from_name": joining.name, "into": keeping.key, "into_name": keeping.name,
                "merchants": joining.merchants, "contractor_carried": carried,
            },
        )
    return {"from": joining, "into": keeping, "contractor_carried": carried}


def unmerge_payee(conn: sqlite3.Connection, business_id: int, text: str) -> Dict:
    """Take a spelling that was merged back out, with the name and flag it had before the merge."""
    spelling = normalize_description(text)
    record = conn.execute("SELECT * FROM payees WHERE business_id = ? AND merchant = ?", (business_id, spelling)).fetchone()
    if record is None or record["merged_into"] is None:
        payee = find_payee(conn, business_id, text)
        merged = [
            row["merchant"]
            for row in conn.execute(
                "SELECT merchant FROM payees WHERE business_id = ? AND merged_into = ? ORDER BY merchant",
                (business_id, payee.record_id or 0),
            )
        ]
        hint = f" Spellings merged into it: {', '.join(merged)}." if merged else ""
        raise BooksError(f"{text!r} was not merged into another payee, so there is nothing to take out.{hint}")
    parent = _records(conn, business_id).get(record["merged_into"])
    if parent is None:
        raise BooksError(f"{spelling} is merged into a payee these books do not hold. Run 'check'.")
    carried = _carried_flag(conn, business_id, spelling, parent["merchant"])
    with transaction(conn):
        conn.execute("UPDATE payees SET merged_into = NULL WHERE id = ?", (record["id"],))
        if carried:
            conn.execute("UPDATE payees SET contractor = 0 WHERE id = ?", (parent["id"],))
        history.record(
            conn, "payee-unmerge", f"{spelling} is its own payee again, apart from {parent['display_name'] or parent['merchant']}.",
            business_id, {"merchant": spelling, "from": parent["merchant"], "contractor_returned": carried},
        )
    return {"merchant": spelling, "from": parent["display_name"] or parent["merchant"], "contractor_returned": carried}


def _carried_flag(conn: sqlite3.Connection, business_id: int, spelling: str, into: str) -> bool:
    """Whether the merge being taken back is what flagged the payee it joined as a contractor."""
    for entry in conn.execute(
        "SELECT detail_json FROM history WHERE kind = 'payee-merge' AND business_id = ? ORDER BY id DESC", (business_id,)
    ):
        detail = json.loads(entry["detail_json"])
        if detail.get("from") == spelling and detail.get("into") == into:
            return bool(detail.get("contractor_carried"))
    return False


def set_contractor(conn: sqlite3.Connection, business_id: int, text: str, contractor: bool) -> Payee:
    payee = find_payee(conn, business_id, text)
    with transaction(conn):
        record = _ensure_record(conn, business_id, payee.key)
        conn.execute("UPDATE payees SET contractor = ? WHERE id = ?", (1 if contractor else 0, record["id"]))
        history.record(
            conn, "payee-set", f"Payee {payee.name} is {'now' if contractor else 'no longer'} flagged as a contractor.",
            business_id, {"merchant": payee.key, "before": payee.contractor, "after": contractor},
        )
    return payee


# Figures

@dataclass
class PayeeYear:
    payee: Payee
    year: int
    rows: int = 0
    money_out: int = 0
    money_in: int = 0


def _by_merchant(conn: sqlite3.Connection, business_id: int, where: str = "", params: tuple = ()) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT t.merchant, CAST(substr(t.posted_on, 1, 4) AS INTEGER) AS year, COUNT(*) AS row_count,"
        " COALESCE(-SUM(CASE WHEN t.amount_cents < 0 THEN t.amount_cents END), 0) AS money_out,"
        " COALESCE(SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents END), 0) AS money_in"
        " FROM transactions t JOIN accounts a ON a.id = t.account_id"
        f" WHERE a.business_id = ? {where} GROUP BY t.merchant, year",
        (business_id, *params),
    ).fetchall()


def payee_years(conn: sqlite3.Connection, business_id: int, year: Optional[int] = None) -> List[PayeeYear]:
    """Rows and money per payee per year, over every row whatever its category."""
    found = directory(conn, business_id)
    totals: Dict[Tuple[str, int], PayeeYear] = {}
    where, params = ("AND substr(t.posted_on, 1, 4) = ?", (f"{year:04d}",)) if year is not None else ("", ())
    for row in _by_merchant(conn, business_id, where, params):
        payee = found[row["merchant"]]
        entry = totals.setdefault((payee.key, row["year"]), PayeeYear(payee, row["year"]))
        entry.rows += row["row_count"]
        entry.money_out += row["money_out"]
        entry.money_in += row["money_in"]
    return sorted(totals.values(), key=lambda entry: (entry.payee.name.lower(), entry.year))


@dataclass
class PayeeTotal:
    payee: Payee
    rows: int = 0
    money_out: int = 0
    money_in: int = 0
    by_card: int = 0

    @property
    def net_paid(self) -> int:
        return self.money_out - self.money_in


@dataclass
class PayeeReport:
    business: str
    period: Period
    totals: List[PayeeTotal]
    waiting_count: int
    waiting_in: int
    waiting_out: int
    left_out_count: int


def payee_report(
    conn: sqlite3.Connection, business: sqlite3.Row, period: Period, tag_id: Optional[int] = None
) -> PayeeReport:
    """Booked money per payee over a period. Transfers and owner money are not payments to anyone, so they are left out.

    With a tag, only its rows count.
    """
    found = directory(conn, business["id"])
    tagged, tag_params = tag_filter(tag_id)
    rows = conn.execute(
        "SELECT t.merchant, t.amount_cents, a.kind AS account_kind, c.kind AS category_kind"
        " FROM transactions t JOIN accounts a ON a.id = t.account_id LEFT JOIN categories c ON c.id = t.category_id"
        " WHERE a.business_id = ? AND t.posted_on BETWEEN ? AND ?" + tagged,
        (business["id"], period.start.isoformat(), period.end.isoformat(), *tag_params),
    ).fetchall()
    totals: Dict[str, PayeeTotal] = {}
    waiting_count = waiting_in = waiting_out = left_out = 0
    for row in rows:
        amount = row["amount_cents"]
        if row["category_kind"] is None:
            waiting_count += 1
            if amount > 0:
                waiting_in += amount
            else:
                waiting_out -= amount
            continue
        if row["category_kind"] in OFF_PAYEE_KINDS:
            left_out += 1
            continue
        payee = found[row["merchant"]]
        entry = totals.setdefault(payee.key, PayeeTotal(payee))
        entry.rows += 1
        if amount < 0:
            entry.money_out -= amount
            if row["account_kind"] == "credit_card":
                entry.by_card -= amount
        else:
            entry.money_in += amount
    return PayeeReport(business["name"], period, list(totals.values()), waiting_count, waiting_in, waiting_out, left_out)


def top(report: PayeeReport, direction: str, limit: int) -> List[PayeeTotal]:
    key = (lambda entry: entry.money_out) if direction == "out" else (lambda entry: entry.money_in)
    chosen = [entry for entry in report.totals if key(entry) > 0]
    return sorted(chosen, key=lambda entry: (-key(entry), entry.payee.name.lower()))[:limit]


CONTRACTORS_NOTICE_LINES = (
    "This is a bookkeeping list to hand your preparer. It is not a determination of who needs",
    "an information return or any other form; your preparer decides that.",
)
CONTRACTORS_NOTICE = " ".join(CONTRACTORS_NOTICE_LINES)
# The preparer folder divides its list of flagged payees here too.
DEFAULT_THRESHOLD = "600.00"


@dataclass
class ContractorReport:
    business: str
    period: Period
    threshold: int
    at_or_over: List[PayeeTotal]
    under: List[PayeeTotal]
    flagged: int
    waiting_count: int
    waiting_out: int


def contractor_report(conn: sqlite3.Connection, business: sqlite3.Row, period: Period, threshold: int) -> ContractorReport:
    """Payments to payees flagged as contractors, divided at the amount the person gives. A list, not a decision."""
    if threshold < 0:
        raise BooksError("the threshold cannot be negative")
    base = payee_report(conn, business, period)
    flagged = [payee for payee in all_payees(conn, business["id"]) if payee.contractor]
    by_key = {entry.payee.key: entry for entry in base.totals}
    chosen = [by_key.get(payee.key) or PayeeTotal(payee) for payee in flagged]
    chosen.sort(key=lambda entry: (-entry.net_paid, entry.payee.name.lower()))
    contractor_merchants = {merchant for payee in flagged for merchant in payee.merchants}
    waiting = conn.execute(
        "SELECT t.merchant, t.amount_cents FROM transactions t JOIN accounts a ON a.id = t.account_id"
        " WHERE a.business_id = ? AND t.category_id IS NULL AND t.posted_on BETWEEN ? AND ?",
        (business["id"], period.start.isoformat(), period.end.isoformat()),
    ).fetchall()
    waiting_rows = [row for row in waiting if row["merchant"] in contractor_merchants]
    return ContractorReport(
        business=business["name"],
        period=period,
        threshold=threshold,
        at_or_over=[entry for entry in chosen if entry.net_paid >= threshold and entry.rows],
        under=[entry for entry in chosen if entry.net_paid < threshold or not entry.rows],
        flagged=len(flagged),
        waiting_count=len(waiting_rows),
        waiting_out=-sum(row["amount_cents"] for row in waiting_rows if row["amount_cents"] < 0),
    )


def payee_of(conn: sqlite3.Connection, business_id: int, merchant: str) -> Payee:
    return directory(conn, business_id)[merchant]


def recent_rows(conn: sqlite3.Connection, business_id: int, payee: Payee, limit: int = 10) -> List[sqlite3.Row]:
    marks = ", ".join("?" * len(payee.merchants))
    return conn.execute(
        "SELECT t.id, t.posted_on, t.amount_cents, t.description, a.name AS account_name, c.code AS category_code,"
        " c.name AS category_name FROM transactions t JOIN accounts a ON a.id = t.account_id"
        f" LEFT JOIN categories c ON c.id = t.category_id WHERE a.business_id = ? AND t.merchant IN ({marks})"
        " ORDER BY t.posted_on DESC, t.id DESC LIMIT ?",
        (business_id, *payee.merchants, limit),
    ).fetchall()


def rows_per_spelling(conn: sqlite3.Connection, business_id: int, payee: Payee) -> Dict[str, int]:
    counts = {merchant: 0 for merchant in payee.merchants}
    for row in _by_merchant(conn, business_id):
        if row["merchant"] in counts:
            counts[row["merchant"]] += row["row_count"]
    return counts


def problems(conn: sqlite3.Connection) -> List[str]:
    """What 'check' reports about payees: a merge into another business's payee, or merges that go round in a circle."""
    found: List[str] = []
    crossed = conn.execute(
        "SELECT COUNT(*) FROM payees p JOIN payees q ON q.id = p.merged_into WHERE q.business_id != p.business_id"
    ).fetchone()[0]
    if crossed:
        found.append(f"{crossed} payee spelling(s) are merged into another business's payee.")
    circles = 0
    for (business_id,) in conn.execute("SELECT DISTINCT business_id FROM payees").fetchall():
        records = _records(conn, business_id)
        for record_id in records:
            try:
                _root(records, record_id)
            except BooksError:
                circles += 1
    if circles:
        found.append(f"{circles} payee spelling(s) are merged in a circle, so no payee holds them.")
    return found
