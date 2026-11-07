"""Reading a bank or card CSV export through a per-account column mapping."""
from __future__ import annotations

import codecs
import csv
import io
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Container, Dict, List, Optional, Tuple

from .errors import BooksError
from .money import parse_cents
from .periods import FIRST_YEAR, LAST_YEAR
from .text import clean_cell

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
    "column_names",
    "direction_column",
    "debit_values",
    "credit_values",
)

# Past this it is not a bank export, and reading it could take all the memory there is.
MAX_FILE_BYTES = 500 * 1024 * 1024
# No bank writes this much in one cell. Rules run over this text, so it stays bounded.
MAX_TEXT = 2000
# How far down a file to look for the header row when it is not where the mapping says.
_HEADER_SEARCH_ROWS = 50

# A label in place of a date marks a summary line, whatever figures follow it.
_SUMMARY_LABEL = re.compile(
    r"^((sub|grand |statement |account |period |page )?totals?\b"
    r"|(beginning|ending|opening|closing|starting|available|current|previous|new|statement|account) balance\b"
    r"|balance\b|end of\b|number of\b)",
    re.IGNORECASE,
)
# Some banks date their balance lines. These are the whole description, so TOTAL WINE stays a purchase.
_DATED_BALANCE_LINE = re.compile(
    r"^((sub|grand |statement |account |period )?totals?"
    r"|(beginning|ending|opening|closing|starting|available|current|previous|new|statement|account) balance"
    r"|balance (forward|brought forward|carried forward))"
    r"( as of .*)?$",
    re.IGNORECASE,
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
    column_names: Optional[List[str]] = None
    direction_column: Optional[str] = None
    debit_values: List[str] = field(default_factory=lambda: ["debit", "dr", "d"])
    credit_values: List[str] = field(default_factory=lambda: ["credit", "cr", "c"])

    def validate(self) -> None:
        for name in _FIELDS:
            if name.endswith(("_column", "_format")) or name in ("delimiter", "encoding"):
                value = getattr(self, name)
                if value is not None and not isinstance(value, str):
                    raise BooksError(f"{name} must be text")
        for name in ("skip_statuses", "debit_values", "credit_values"):
            value = getattr(self, name)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise BooksError(f"{name} must be a list of text values")
        if self.column_names is not None:
            if (
                not isinstance(self.column_names, list)
                or not self.column_names
                or not all(isinstance(item, str) for item in self.column_names)
            ):
                raise BooksError("column_names must be a list of text values")
        has_amount = bool(self.amount_column)
        has_pair = bool(self.debit_column) and bool(self.credit_column)
        if has_amount == has_pair or (has_amount and (self.debit_column or self.credit_column)):
            raise BooksError("a mapping needs either amount_column, or both debit_column and credit_column")
        if not self.date_column or not self.description_column:
            raise BooksError("a mapping needs date_column and description_column")
        if not self.date_format:
            raise BooksError("a mapping needs date_format")
        if self.delimiter is None or len(self.delimiter) != 1 or self.delimiter in ('"', "\r", "\n"):
            raise BooksError("delimiter must be one character")
        if isinstance(self.skip_lines, bool) or not isinstance(self.skip_lines, int) or self.skip_lines < 0:
            raise BooksError("skip_lines must be a whole number, zero or more")
        if not isinstance(self.invert_amount, bool):
            raise BooksError("invert_amount must be true or false, without quotes")
        if self.direction_column and not self.amount_column:
            raise BooksError("direction_column goes with amount_column")
        if self.encoding:
            try:
                codecs.lookup(self.encoding)
            except LookupError:
                raise BooksError(f"unknown encoding {self.encoding!r}. Try utf-8 or cp1252.") from None
        try:
            datetime(2001, 2, 3).strftime(self.date_format)
        except ValueError:
            raise BooksError(f"date_format {self.date_format!r} is not a usable date format") from None

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
        except UnicodeDecodeError:
            raise BooksError(f"mapping file {path} is not UTF-8 text") from None
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


def read_bytes(path: Path) -> bytes:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise BooksError(f"{path.name} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB, which is not a normal export.")
        return path.read_bytes()
    except OSError as exc:
        raise BooksError(f"cannot read {path}: {exc.strerror}") from None
    except MemoryError:
        raise BooksError(f"{path.name} is too large to read on this computer.") from None


def _wrong_kind(data: bytes) -> Optional[str]:
    """Name a file that is plainly not a text export, so the message can say what to do instead."""
    start = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if data.startswith(b"PK\x03\x04"):
        return "a compressed file or an Excel workbook. Save or download it as CSV"
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return "an old Excel workbook. Save or download it as CSV"
    if data.startswith(b"%PDF"):
        return "a PDF. Download the transactions as CSV or OFX from your bank instead of the statement PDF"
    if data.startswith(b"SQLite format 3"):
        return "a database, not an export"
    if start.startswith((b"<!doctype html", b"<html")):
        return "a web page, often a sign-in page saved in place of the download. Download the file again"
    if start.startswith(b"!type:") or start.startswith(b"!account"):
        return "a QIF file, which is not read here. Choose CSV or OFX/QFX in your bank's download options"
    return None


def decode(data: bytes, name: str, encoding: Optional[str] = None) -> str:
    """Bytes to text: a stated encoding, or by byte order mark, then UTF-8, then Windows-1252."""
    wrong = _wrong_kind(data)
    if wrong:
        raise BooksError(f"{name} is {wrong}.")
    if encoding:
        try:
            text = data.decode(encoding)
        except (LookupError, UnicodeDecodeError) as exc:
            raise BooksError(f"cannot read {name} as {encoding}: {exc}") from None
    elif data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        text = _decode_or_refuse(data, "utf-32", name)
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = _decode_or_refuse(data, "utf-16", name)
    elif _looks_like_bare_utf16(data):
        text = _decode_or_refuse(data, "utf-16-le" if data[1] == 0 else "utf-16-be", name)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    if "\x00" in text:
        raise BooksError(f"{name} holds binary data, so it is not a text export. Download it again as CSV.")
    return text


def _decode_or_refuse(data: bytes, encoding: str, name: str) -> str:
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise BooksError(f"{name} looks like {encoding} but cannot be read as it: {exc.reason}.") from None


def _looks_like_bare_utf16(data: bytes) -> bool:
    """UTF-16 without a byte order mark: ordinary text has a zero in every other byte."""
    sample = data[:400]
    if len(sample) < 8:
        return False
    return sample[1::2].count(0) * 10 > len(sample[1::2]) * 9 or sample[0::2].count(0) * 10 > len(sample[0::2]) * 9


def read_text(path: Path, encoding: Optional[str]) -> str:
    return decode(read_bytes(path), Path(path).name, encoding)


def read_rows(text: str, delimiter: str, name: str) -> List[Tuple[int, List[str]]]:
    """Every row with the line it starts on. A file the CSV reader cannot follow stops here, plainly."""
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows: List[Tuple[int, List[str]]] = []
    start = 1
    try:
        for row in reader:
            rows.append((start, row))
            start = reader.line_num + 1
    except csv.Error as exc:
        raise BooksError(f"{name} line {reader.line_num}: this is not a well-formed CSV file ({exc}).") from None
    if rows and reader.line_num > rows[-1][0]:
        _check_last_row_closes(text, delimiter, name, rows[-1][0])
    return rows


def _check_last_row_closes(text: str, delimiter: str, name: str, line: int) -> None:
    """A quote that never closes makes one row out of everything after it. That must not pass as a row."""
    tail = "".join(io.StringIO(text, newline="").readlines()[line - 1:])
    try:
        list(csv.reader(io.StringIO(tail, newline=""), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        if "unexpected end" in str(exc).lower():
            raise BooksError(
                f"{name} line {line}: a quotation mark opens here and never closes, so the rest of the file "
                "cannot be read as rows. The download may be cut short. Nothing was read from it."
            ) from None


def _header_index(header: List[str]) -> Dict[str, int]:
    index: Dict[str, int] = {}
    for position, name in enumerate(header):
        index.setdefault(name.strip().lower(), position)
    return index


def _column(index: Dict[str, int], name: Optional[str], header: List[str], file_name: str) -> Optional[int]:
    if not name:
        return None
    position = index.get(name.strip().lower())
    if position is None:
        found = ", ".join(repr(clean_cell(h)[:60]) for h in header[:40])
        raise BooksError(f"{file_name} has no column {name!r}. Columns found: {found}")
    return position


def _without_trailing_blanks(row: List[str]) -> List[str]:
    end = len(row)
    while end and not row[end - 1].strip():
        end -= 1
    return row[:end]


def _cell(row: List[str], position: Optional[int]) -> str:
    if position is None or position >= len(row):
        return ""
    return row[position].strip()


def _amount(
    row: List[str],
    amount: Optional[int],
    debit: Optional[int],
    credit: Optional[int],
    invert: bool,
    direction: Optional[int] = None,
    debit_values: Container[str] = (),
    credit_values: Container[str] = (),
) -> Optional[int]:
    """Signed cents, positive for money in. None when the row holds no amount at all."""
    if amount is not None:
        text = _cell(row, amount)
        if not text:
            return None
        cents = parse_cents(text)
        if direction is not None:
            word = _cell(row, direction).lower()
            if word in debit_values:
                cents = -abs(cents)
            elif word in credit_values:
                cents = abs(cents)
            else:
                raise BooksError(f"cannot tell money in from money out: {_cell(row, direction)!r}")
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


def _mapped_columns(mapping: Mapping) -> List[str]:
    names = (
        mapping.date_column, mapping.description_column, mapping.amount_column, mapping.debit_column,
        mapping.credit_column, mapping.memo_column, mapping.reference_column, mapping.status_column,
        mapping.direction_column,
    )
    return [name.strip().lower() for name in names if name]


def _find_header(rows: List[Tuple[int, List[str]]], start: int, mapping: Mapping) -> int:
    """The row named by skip_lines, unless a later one is the one naming every mapped column.

    Banks vary the lines above the header from one download to the next, so this does not depend on counting them.
    """
    while start < len(rows) and not any(cell.strip() for cell in rows[start][1]):
        start += 1
    wanted = _mapped_columns(mapping)
    for position in range(start, min(start + _HEADER_SEARCH_ROWS, len(rows))):
        names = {cell.strip().lower() for cell in rows[position][1]}
        if all(name in names for name in wanted):
            return position
    return start


def parse_export(path: Path, mapping: Mapping) -> ParsedFile:
    return parse_text(read_text(path, mapping.encoding), mapping, Path(path).name)


def parse_text(text: str, mapping: Mapping, name: str) -> ParsedFile:
    """Read every data row. Bad rows are collected, not raised, so one report covers them all."""
    rows = read_rows(text, mapping.delimiter, name)
    if mapping.column_names:
        header = list(mapping.column_names)
        body = rows[mapping.skip_lines:]
    else:
        body_start = _find_header(rows, mapping.skip_lines, mapping)
        if body_start >= len(rows):
            raise BooksError(f"{name} has no header row (skip_lines is {mapping.skip_lines})")
        header = rows[body_start][1]
        body = rows[body_start + 1:]

    index = _header_index(header)
    date_col = _column(index, mapping.date_column, header, name)
    desc_col = _column(index, mapping.description_column, header, name)
    amount_col = _column(index, mapping.amount_column, header, name)
    debit_col = _column(index, mapping.debit_column, header, name)
    credit_col = _column(index, mapping.credit_column, header, name)
    memo_col = _column(index, mapping.memo_column, header, name)
    ref_col = _column(index, mapping.reference_column, header, name)
    status_col = _column(index, mapping.status_column, header, name)
    direction_col = _column(index, mapping.direction_column, header, name)
    skip_statuses = {s.strip().lower() for s in mapping.skip_statuses}
    debit_values = {s.strip().lower() for s in mapping.debit_values}
    credit_values = {s.strip().lower() for s in mapping.credit_values}
    width = len(_without_trailing_blanks(header))

    parsed = ParsedFile()
    for offset, row in body:
        if not any(cell.strip() for cell in row):
            continue
        parsed.data_rows += 1
        if status_col is not None and _cell(row, status_col).lower() in skip_statuses:
            parsed.skipped += 1
            continue
        date_text = _cell(row, date_col)
        dated = bool(date_text) and _fits(date_text, mapping.date_format)
        filled = _without_trailing_blanks(row)
        label = next((cell.strip() for cell in filled if cell.strip()), "")
        if not dated and _SUMMARY_LABEL.match(label):
            parsed.skipped += 1
            continue
        if dated and _DATED_BALANCE_LINE.match(" ".join(_cell(row, desc_col).split())):
            parsed.skipped += 1
            continue
        if len(filled) > width:
            parsed.errors.append(
                f"line {offset}: {len(filled)} fields but the header has {width}. "
                "An amount such as 1,064.33 may be missing its quotes."
            )
            continue
        try:
            amount = _amount(
                row, amount_col, debit_col, credit_col, mapping.invert_amount, direction_col, debit_values, credit_values
            )
        except BooksError as exc:
            parsed.errors.append(f"line {offset}: {exc}")
            continue
        # Any other line with no amount and nothing that reads as a date is a note from the bank.
        if amount is None:
            if dated:
                parsed.errors.append(f"line {offset}: date but no amount")
            else:
                parsed.skipped += 1
            continue
        if not date_text:
            parsed.errors.append(f"line {offset}: amount but no date")
            continue
        if not dated:
            parsed.errors.append(f"line {offset}: date {date_text!r} does not fit format {mapping.date_format}")
            continue
        posted_on = datetime.strptime(date_text, mapping.date_format).date()
        if not FIRST_YEAR <= posted_on.year <= LAST_YEAR:
            parsed.errors.append(f"line {offset}: date {date_text!r} reads as the year {posted_on.year}")
            continue
        # Zero rows are bank notices. They do not move a balance.
        if amount == 0:
            parsed.skipped += 1
            continue
        description = clean_cell(_cell(row, desc_col))
        if not description:
            parsed.errors.append(f"line {offset}: no description")
            continue
        memo, reference = clean_cell(_cell(row, memo_col)), clean_cell(_cell(row, ref_col))
        if max(len(description), len(memo), len(reference)) > MAX_TEXT:
            parsed.errors.append(f"line {offset}: a cell is longer than {MAX_TEXT} characters, which no bank writes")
            continue
        parsed.rows.append(ParsedRow(offset, posted_on, amount, description, memo, reference))
    return parsed


_DATE_NAMES = ("posting date", "posted date", "post date", "posted", "date", "transaction date", "trans date", "trans. date")
_DESCRIPTION_NAMES = (
    "description",
    "desc",
    "payee",
    "merchant",
    "name",
    "details",
    "transaction description",
    "transaction detail",
    "transaction details",
    "memo",
)
_AMOUNT_NAMES = ("amount", "transaction amount", "amt")
_DEBIT_NAMES = ("debit", "debits", "withdrawal", "withdrawals", "money out", "charge", "charges")
_CREDIT_NAMES = ("credit", "credits", "deposit", "deposits", "money in", "payment", "payments")
_REFERENCE_NAMES = ("transaction id", "reference", "reference number", "ref", "ref.", "fitid", "id", "check number", "check or slip #")
_DIRECTION_NAMES = ("credit debit indicator", "debit/credit", "credit/debit", "dr/cr", "cr/dr", "indicator")
_STATUS_NAMES = ("status", "state")


def _find_name(names: Tuple[str, ...], lowered: List[str]) -> Optional[int]:
    for wanted in names:
        if wanted in lowered:
            return lowered.index(wanted)
    return None


def suggest_mapping(path: Path) -> Tuple[Mapping, List[str]]:
    """Guess a mapping from a sample export. Returns the guess and what to double-check."""
    text = read_text(path, None)
    # Look past the first line, because a bank's preamble may hold no delimiter at all.
    opening = "\n".join(text.split("\n")[:20])
    delimiter = max((",", ";", "\t", "|"), key=opening.count) if opening else ","
    rows = [row for _, row in read_rows(text, delimiter, Path(path).name)]
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
        direction_at = _find_name(_DIRECTION_NAMES, lowered)
        if direction_at is not None:
            mapping.direction_column = header[direction_at]
            notes.append(f"Using {header[direction_at]!r} to tell money in from money out. Check the sign in a dry run.")
        else:
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

    # Footers such as "Total" sit in the date column too, and every real date starts with a digit.
    samples = [row[date_at].strip() for row in rows[header_at + 1:] if len(row) > date_at and row[date_at].strip()[:1].isdigit()]
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
