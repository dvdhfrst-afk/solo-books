"""Reading OFX, QFX and QBO downloads, the format most banks offer for money software.

Older files are SGML and newer ones XML. Tags are read with plain patterns, never an XML parser, so a
hostile file has no entities or outside references to expand.
"""
from __future__ import annotations

import html
import re
from datetime import date
from typing import Optional

from .csvmap import MAX_TEXT, ParsedFile, ParsedRow
from .errors import BooksError
from .money import parse_cents
from .periods import FIRST_YEAR, LAST_YEAR
from .text import clean_cell

# What an import records in place of a column mapping, so a later re-read can be told apart from this one.
OFX_MAPPING_JSON = '{"format": "ofx"}'

_OPENING = re.compile(r"^\s*(?:<\?xml[^>]{0,200}\?>\s*)?(?:<\?OFX|OFXHEADER|<OFX\b)", re.IGNORECASE)
_ROW = re.compile(r"<STMTTRN>(.*?)(?=</STMTTRN>|<STMTTRN>|</BANKTRANLIST>|\Z)", re.IGNORECASE | re.DOTALL)
_ACCOUNT = re.compile(r"<ACCTID>\s*([^<\r\n]*)", re.IGNORECASE)
_CURRENCY = re.compile(r"<CURDEF>\s*([^<\r\n]*)", re.IGNORECASE)
_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})")


def looks_like_ofx(text: str) -> bool:
    return bool(_OPENING.match(text[:2048]))


def _value(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}>\s*([^<\r\n]*)", block, re.IGNORECASE)
    return clean_cell(html.unescape(match.group(1))) if match else ""


def _read_date(text: str) -> Optional[date]:
    match = _DATE.match(text)
    if not match:
        return None
    try:
        day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return day if FIRST_YEAR <= day.year <= LAST_YEAR else None


def parse_ofx(text: str, name: str) -> ParsedFile:
    """Every statement row in the file. OFX signs amounts from the account's side, as the books do."""
    accounts = {clean_cell(found) for found in _ACCOUNT.findall(text)}
    if len(accounts) > 1:
        raise BooksError(f"{name} holds {len(accounts)} accounts. Download each account's file separately.")
    currencies = {clean_cell(found).upper() for found in _CURRENCY.findall(text)} - {""}
    if currencies - {"USD"}:
        raise BooksError(
            f"{name} is in {', '.join(sorted(currencies))}. These books hold US dollars, and converting is a decision "
            "for a person. Nothing was read from it."
        )
    parsed = ParsedFile()
    line, position = 1, 0
    for match in _ROW.finditer(text):
        line += text.count("\n", position, match.start())
        position = match.start()
        block = match.group(1)
        parsed.data_rows += 1
        posted_on = _read_date(_value(block, "DTPOSTED"))
        if posted_on is None:
            parsed.errors.append(f"line {line}: no usable DTPOSTED date")
            continue
        try:
            amount = parse_cents(_value(block, "TRNAMT"))
        except BooksError as exc:
            parsed.errors.append(f"line {line}: {exc}")
            continue
        if amount == 0:
            parsed.skipped += 1
            continue
        title, memo = _value(block, "NAME"), _value(block, "MEMO")
        description = title or memo
        if not description:
            parsed.errors.append(f"line {line}: no NAME or MEMO to describe the row")
            continue
        memo = "" if memo == description else memo
        reference = _value(block, "FITID")
        if max(len(description), len(memo), len(reference)) > MAX_TEXT:
            parsed.errors.append(f"line {line}: a value is longer than {MAX_TEXT} characters, which no bank writes")
            continue
        parsed.rows.append(ParsedRow(line, posted_on, amount, description, memo, reference))
    return parsed
