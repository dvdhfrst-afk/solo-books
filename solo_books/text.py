"""Cleaning up the text banks put in a description field."""
from __future__ import annotations

import re

# Wrapping text card networks and payment processors put in front of the merchant.
_PREFIXES = (
    "POS DEBIT",
    "POS PURCHASE",
    "POS",
    "DEBIT CARD PURCHASE",
    "CARD PURCHASE",
    "CHECKCARD",
    "PURCHASE AUTHORIZED ON",
    "PURCHASE",
    "RECURRING PAYMENT",
    "ACH DEBIT",
    "ACH CREDIT",
    "ACH",
    "SQ *",
    "SQ*",
    "TST*",
    "TST *",
    "PAYPAL *",
    "PP*",
)

_MAX_MERCHANT_WORDS = 3

# Bytes that steer a terminal or reorder text on screen. A hostile file must not be able to do either.
_INVISIBLE = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]")
_LINE_BREAKS = re.compile("[\\t\\r\\n\\u2028\\u2029]+")
_PLAIN_NUMBER = re.compile(r"^[-+]?\d[\d,]*(\.\d+)?$")


def printable(text: str) -> str:
    """Drop control characters but keep line breaks, for anything about to reach a terminal."""
    return _INVISIBLE.sub("", text)


def clean_cell(text: str) -> str:
    """One line of plain text from whatever a bank's file held in a cell."""
    return _LINE_BREAKS.sub(" ", _INVISIBLE.sub("", text)).strip()


def normalize_description(text: str) -> str:
    """Uppercase with runs of whitespace collapsed; the form used for comparing rows."""
    return " ".join(text.upper().split())


def merchant_key(description: str) -> str:
    """Best guess at the merchant, so rows from one payee group together.

    SQ *BLUE FERN CAFE 0423 OGDEN UT and SQ *BLUE FERN CAFE 0511 OGDEN UT both give
    BLUE FERN CAFE. A description with nothing left after cleaning is returned whole.
    """
    cleaned = normalize_description(description)
    stripped = True
    while stripped:
        stripped = False
        for prefix in _PREFIXES:
            if cleaned.startswith(prefix + " ") or (prefix.endswith("*") and cleaned.startswith(prefix)):
                cleaned = cleaned[len(prefix):].lstrip()
                stripped = True
                break
    words = []
    for word in cleaned.split():
        if re.search(r"\d", word) or word in ("#", "-", "*"):
            if words:
                break
            continue
        words.append(word.strip("*#,."))
        if len(words) == _MAX_MERCHANT_WORDS:
            break
    key = " ".join(w for w in words if w)
    return key or normalize_description(description)


def spreadsheet_safe(text: str) -> str:
    """Stop a description from being run as a formula when a CSV opens in a spreadsheet."""
    if text.lstrip(" \t\r\n")[:1] in ("=", "+", "-", "@") or text[:1] in ("\t", "\r"):
        return "'" + text
    return text


def spreadsheet_cell(text: str) -> str:
    """The same, for a cell that may hold a plain number, which must stay a number."""
    return text if _PLAIN_NUMBER.match(text) else spreadsheet_safe(text)


def from_spreadsheet_safe(text: str) -> str:
    """Undo spreadsheet_safe, for a file this program wrote and is reading back."""
    if text.startswith("'") and text[1:].lstrip(" \t\r\n")[:1] in ("=", "+", "-", "@"):
        return text[1:]
    return text


def slug(text: str) -> str:
    """Business A becomes business-a, for file names. Letters of any alphabet stay; separators never do."""
    return re.sub(r"[\W_]+", "-", text.lower()).strip("-") or "business"
