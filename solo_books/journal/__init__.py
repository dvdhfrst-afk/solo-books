"""The books as journals that other accounting programs read. One module per format, listed in _MODULES.

Each format module has NAME, HELP, BYTE_ORDER_MARK and a render(journal) function.
"""
from __future__ import annotations

from typing import Tuple

from . import beancount, ledger_text, postings_csv, qif
from .entries import Journal, build_journal

_MODULES = (ledger_text, beancount, qif, postings_csv)

FORMATS = {module.NAME: module for module in _MODULES}


def names() -> Tuple[str, ...]:
    return tuple(FORMATS)


def help_text() -> str:
    return "; ".join(f"{module.NAME}: {module.HELP}" for module in _MODULES)


__all__ = ["FORMATS", "Journal", "build_journal", "help_text", "names"]
