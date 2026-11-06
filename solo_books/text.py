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
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def slug(text: str) -> str:
    """Business A becomes business-a, for file names."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "business"
