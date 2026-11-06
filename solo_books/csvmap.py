"""Reading a bank or card CSV export through a per-account column mapping."""
from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .errors import BooksError
from .money import parse_cents

_FIELDS = (
    "date_column",
    "date_format",
    "description_column",
    "amount_column",
    "debit_column",
    "credit_column",
    "invert_amount",
    "memo_column",
    "reference_column",
    "status_column",
    "skip_statuses",
    "skip_lines",
    "delimiter",
    "encoding",
)

_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%Y", "%d/%m/%Y", "%b %d, %Y", "%d %b %Y")


@dataclass
class Mapping:
    """How to read one account's export."""

    date_column: str = "Date"
    date_format: str = "%m/%d/%Y"
    description_column: str = "Description"
    amount_column: Optional[str] = "Amount"
    debit_column: Optional[str] = None
    credit_column: Optional[str] = None
    invert_amount: bool = False
    memo_column: Optional[str] = None
    reference_column: Optional[str] = None
    status_column: Optional[str] = None
    skip_statuses: List[str] = field(default_factory=lambda: ["pending"])
    skip_lines: int = 0
    delimiter: str = ","
    encoding: Optional[str] = None

    def validate(self) -> None:
        has_amount = bool(self.amount_column)
        has_pair = bool(self.debit_column) and bool(self.credit_column)
        if has_amount == has_pair or (has_amount and (self.debit_column or self.credit_column)):
            raise BooksError("a mapping needs either amount_column, or both debit_column and credit_column")
        if not self.date_column or not self.description_column:
            raise BooksError("a mapping needs date_column and description_column")
        if not self.date_format:
            raise BooksError("a mapping needs date_format")
        if len(self.delimiter) != 1:
            raise BooksError("delimiter must be one character")
        if not isinstance(self.skip_lines, int) or self.skip_lines < 0:
            raise BooksError("skip_lines must be a whole number, zero or more")
        if not isinstance(self.skip_statuses, list):
            raise BooksError("skip_statuses must be a list")

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1)

    @classmethod
    def from_dict(cls, raw: dict) -> "Mapping":
        unknown = sorted(set(raw) - set(_FIELDS))
        if unknown:
            raise BooksError(f"unknown mapping field(s): {', '.join(unknown)} (known: {', '.join(_FIELDS)})")
        # A mapping that names debit and credit columns means no single amount column.
        if raw.get("debit_column") and "amount_column" not in raw:
            raw = dict(raw, amount_column=None)
        mapping = cls(**raw)
        mapping.validate()
        return mapping

    @classmethod
    def from_json(cls, text: str) -> "Mapping":
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise BooksError(f"mapping is not valid JSON: {exc}") from None
        if not isinstance(raw, dict):
            raise BooksError("mapping must be a JSON object")
        return cls.from_dict(raw)

    @classmethod
    def from_file(cls, path: Path) -> "Mapping":
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise BooksError(f"cannot read mapping file {path}: {exc.strerror}") from None
        return cls.from_json(text)


@dataclass
class ParsedRow:
    line: int
    posted_on: date
    amount_cents: int
    description: str
    memo: str = ""
    reference: str = ""


@dataclass
class ParsedFile:
    rows: List[ParsedRow] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped: int = 0
    data_rows: int = 0


def read_text(path: Path, encoding: Optional[str]) -> str:
    """Decode an export. UTF-8 (with or without a byte order mark) first, then Windows-1252."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise BooksError(f"cannot read {path}: {exc.strerror}") from None
    if encoding:
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError) as exc:
            raise BooksError(f"cannot read {path} as {encoding}: {exc}") from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _header_index(header: List[str]) -> Dict[str, int]:
    index: Dict[str, int] = {}
    for position, name in enumerate(header):
        index.setdefault(name.strip().lower(), position)
    return index


def _column(index: Dict[str, int], name: Optional[str], header: List[str], path: Path) -> Optional[int]:
    if not name:
        return None
    position = index.get(name.strip().lower())
    if position is None:
        found = ", ".join(repr(h.strip()) for h in header)
        raise BooksError(f"{Path(path).name} has no column {name!r}. Columns found: {found}")
    return position


def _cell(row: List[str], position: Optional[int]) -> str:
    if position is None or position >= len(row):
        return ""
    return row[position].strip()


def _amount(row: List[str], amount: Optional[int], debit: Optional[int], credit: Optional[int], invert: bool) -> Optional[int]:
    """Signed cents, positive for money in. None when the row holds no amount at all."""
    if amount is not None:
        text = _cell(row, amount)
        if not text:
            return None
        cents = parse_cents(text)
    else:
        debit_text, credit_text = _cell(row, debit), _cell(row, credit)
        if not debit_text and not credit_text:
            return None
        # Debit columns are money out however the bank signs them.
        debit_cents = abs(parse_cents(debit_text)) if debit_text else 0
        credit_cents = abs(parse_cents(credit_text)) if credit_text else 0
        if debit_cents and credit_cents:
            raise BooksError("row has both a debit and a credit")
        cents = credit_cents - debit_cents
    return -cents if invert else cents


def parse_export(path: Path, mapping: Mapping) -> ParsedFile:
    """Read every data row. Bad rows are collected, not raised, so one report covers them all."""
    text = read_text(path, mapping.encoding)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=mapping.delimiter)
    rows = list(reader)
    body_start = mapping.skip_lines
    while body_start < len(rows) and not any(cell.strip() for cell in rows[body_start]):
        body_start += 1
    if body_start >= len(rows):
        raise BooksError(f"{Path(path).name} has no header row (skip_lines is {mapping.skip_lines})")

    header = rows[body_start]
    index = _header_index(header)
    date_col = _column(index, mapping.date_column, header, path)
    desc_col = _column(index, mapping.description_column, header, path)
    amount_col = _column(index, mapping.amount_column, header, path)
    debit_col = _column(index, mapping.debit_column, header, path)
    credit_col = _column(index, mapping.credit_column, header, path)
    memo_col = _column(index, mapping.memo_column, header, path)
    ref_col = _column(index, mapping.reference_column, header, path)
    status_col = _column(index, mapping.status_column, header, path)
    skip_statuses = {s.strip().lower() for s in mapping.skip_statuses}

    parsed = ParsedFile()
    for offset, row in enumerate(rows[body_start + 1:], start=body_start + 2):
        if not any(cell.strip() for cell in row):
            continue
        parsed.data_rows += 1
        if status_col is not None and _cell(row, status_col).lower() in skip_statuses:
            parsed.skipped += 1
            continue
        date_text = _cell(row, date_col)
        try:
            amount = _amount(row, amount_col, debit_col, credit_col, mapping.invert_amount)
        except BooksError as exc:
            parsed.errors.append(f"line {offset}: {exc}")
            continue
        # Footers such as "Total" or "End of statement" have no amount and nothing that reads as a date.
        if amount is None:
            if date_text and _fits(date_text, mapping.date_format):
                parsed.errors.append(f"line {offset}: date but no amount")
            else:
                parsed.skipped += 1
            continue
        if not date_text:
            parsed.errors.append(f"line {offset}: amount but no date")
            continue
        try:
            posted_on = datetime.strptime(date_text, mapping.date_format).date()
        except ValueError:
            parsed.errors.append(f"line {offset}: date {date_text!r} does not fit format {mapping.date_format}")
            continue
        # Zero rows are bank notices. They do not move a balance.
        if amount == 0:
            parsed.skipped += 1
            continue
        description = _cell(row, desc_col)
        if not description:
            parsed.errors.append(f"line {offset}: no description")
            continue
        parsed.rows.append(
            ParsedRow(offset, posted_on, amount, description, _cell(row, memo_col), _cell(row, ref_col))
        )
    return parsed


_DATE_NAMES = ("posting date", "posted date", "post date", "posted", "date", "transaction date", "trans date", "trans. date")
_DESCRIPTION_NAMES = ("description", "payee", "merchant", "name", "details", "transaction description", "memo")
_AMOUNT_NAMES = ("amount", "transaction amount", "amt")
_DEBIT_NAMES = ("debit", "debits", "withdrawal", "withdrawals", "money out", "charge", "charges")
_CREDIT_NAMES = ("credit", "credits", "deposit", "deposits", "money in", "payment", "payments")
_REFERENCE_NAMES = ("transaction id", "reference", "reference number", "ref", "ref.", "fitid", "id", "check number", "check or slip #")
_STATUS_NAMES = ("status", "state")


def _find_name(names: Tuple[str, ...], lowered: List[str]) -> Optional[int]:
    for wanted in names:
        if wanted in lowered:
            return lowered.index(wanted)
    return None


def suggest_mapping(path: Path) -> Tuple[Mapping, List[str]]:
    """Guess a mapping from a sample export. Returns the guess and what to double-check."""
    text = read_text(path, None)
    first_line = text.split("\n", 1)[0]
    delimiter = max((",", ";", "\t", "|"), key=first_line.count) if first_line else ","
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not rows:
        raise BooksError(f"{Path(path).name} is empty")

    # The header is the first row naming both a date column and something that holds an amount.
    header_at = None
    for position, row in enumerate(rows[:20]):
        lowered = [cell.strip().lower() for cell in row]
        has_date = _find_name(_DATE_NAMES, lowered) is not None
        has_money = _find_name(_AMOUNT_NAMES, lowered) is not None or (
            _find_name(_DEBIT_NAMES, lowered) is not None and _find_name(_CREDIT_NAMES, lowered) is not None
        )
        if has_date and has_money:
            header_at = position
            break
    if header_at is None:
        raise BooksError(
            f"could not find a header row in {Path(path).name}. "
            "Look at the file and write the mapping by hand; see the README."
        )

    header = [cell.strip() for cell in rows[header_at]]
    lowered = [cell.lower() for cell in header]
    notes: List[str] = []
    mapping = Mapping(skip_lines=header_at, delimiter=delimiter)
    date_at = _find_name(_DATE_NAMES, lowered)
    mapping.date_column = header[date_at]
    description_at = _find_name(_DESCRIPTION_NAMES, lowered)
    if description_at is None:
        raise BooksError(f"could not find a description column among: {', '.join(header)}")
    mapping.description_column = header[description_at]

    amount_at = _find_name(_AMOUNT_NAMES, lowered)
    if amount_at is not None:
        mapping.amount_column = header[amount_at]
        notes.append("Check the sign in a dry run: charges must show as money out. If not, set invert_amount to true.")
    else:
        mapping.amount_column = None
        mapping.debit_column = header[_find_name(_DEBIT_NAMES, lowered)]
        mapping.credit_column = header[_find_name(_CREDIT_NAMES, lowered)]

    reference_at = _find_name(_REFERENCE_NAMES, lowered)
    if reference_at is not None and lowered[reference_at] not in ("check number", "check or slip #"):
        mapping.reference_column = header[reference_at]
        notes.append(
            f"Using {header[reference_at]!r} as a unique row id. Remove reference_column if it is blank or reused."
        )
    status_at = _find_name(_STATUS_NAMES, lowered)
    if status_at is not None:
        mapping.status_column = header[status_at]

    samples = [row[date_at].strip() for row in rows[header_at + 1:] if len(row) > date_at and row[date_at].strip()]
    fits = [fmt for fmt in _DATE_FORMATS if samples and all(_fits(s, fmt) for s in samples)]
    if fits:
        mapping.date_format = fits[0]
        if "%m/%d/%Y" in fits and "%d/%m/%Y" in fits:
            notes.append("Dates fit both month/day and day/month. Assumed month/day/year.")
    else:
        notes.append(f"No known date format fits samples such as {samples[:1]}. Set date_format by hand.")
    mapping.validate()
    return mapping, notes


def _fits(text: str, fmt: str) -> bool:
    try:
        datetime.strptime(text, fmt)
    except ValueError:
        return False
    return True
