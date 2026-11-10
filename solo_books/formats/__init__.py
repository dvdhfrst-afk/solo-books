"""The ways a report can be written. Every report command offers the formats listed here.

To add a format, write a module in this folder with a Format subclass and a FORMAT instance, and add the module to
_MODULES. Its name then shows up in every report's --format choices.
"""
from __future__ import annotations

from typing import Dict, Tuple

from ..errors import BooksError
from . import csv_format, html_page, json_format, markdown, table
from .base import Format, Report

_MODULES = (table, csv_format, json_format, html_page, markdown)

FORMATS: Dict[str, Format] = {module.FORMAT.name: module.FORMAT for module in _MODULES}


def names() -> Tuple[str, ...]:
    return tuple(FORMATS)


def get(name: str) -> Format:
    try:
        return FORMATS[name]
    except KeyError:
        raise BooksError(f"no report format {name!r} (have: {', '.join(FORMATS)})") from None


def help_text() -> str:
    return "; ".join(f"{fmt.name}: {fmt.help}" for fmt in FORMATS.values())


__all__ = ["FORMATS", "Format", "Report", "get", "help_text", "names"]
