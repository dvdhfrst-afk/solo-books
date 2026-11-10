"""What every report format shares: the report it is handed, and the printable document most formats lay it out as.

A report is worked out once into a Report. A format only turns that into text. The table and CSV formats read the
report's own figures; HTML and Markdown read the Document that layout.py builds from it, so a new printable format
needs no knowledge of any one report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set, Union

from .. import __version__
from ..money import plain_cents
from ..periods import Period

# Every report, in the order help text lists them.
REPORT_KINDS = ("pnl", "balances", "schedule-c", "cashflow", "trend", "categories")

GENERATOR = f"solo-books {__version__}"


@dataclass
class Report:
    """One report for one or more businesses, worked out and ready to render.

    parts holds one result per business: Pnl, Balances, ScheduleC, Cashflow, Trend or CategoryStats.
    period is the stretch of time asked for, used by the all-businesses summary where there is one.
    tag names the tag the rows were limited to, when they were.
    """

    kind: str
    parts: list
    period: Optional[Period] = None
    all_lines: bool = False
    tag: Optional[str] = None

    @property
    def only(self) -> str:
        """The sentence that says the figures are one tag's rows, or nothing."""
        return f"Only rows tagged {self.tag}." if self.tag else ""

    def __post_init__(self) -> None:
        if self.kind not in REPORT_KINDS:
            raise ValueError(f"unknown report kind {self.kind!r}")
        if not self.parts:
            raise ValueError("a report needs at least one business")


class Format:
    """A way of writing a report. Subclasses set name and help and write render."""

    name = ""
    help = ""
    # A byte order mark helps a spreadsheet read accents in a CSV file. It breaks JSON readers, so only some formats want it.
    byte_order_mark = False

    def render(self, report: Report) -> str:
        raise NotImplementedError


# The printable document

@dataclass
class Table:
    """Rows of text cells. numeric columns are right-aligned. styles gives each row "", "group" or "total"."""

    headers: List[str]
    rows: List[List[str]] = field(default_factory=list)
    numeric: Set[int] = field(default_factory=set)
    styles: List[str] = field(default_factory=list)

    def add(self, cells: List[str], style: str = "") -> None:
        self.rows.append(cells)
        self.styles.append(style)


@dataclass
class Paragraph:
    text: str
    warning: bool = False


Block = Union[Table, Paragraph]


@dataclass
class Section:
    heading: str
    intro: List[str] = field(default_factory=list)
    blocks: List[Block] = field(default_factory=list)


@dataclass
class Document:
    title: str
    sections: List[Section] = field(default_factory=list)


# JSON pieces shared by the JSON format and the preparer summary

def money(cents: int) -> dict:
    """An amount both ways: whole cents to compute with, and the decimal a person reads."""
    return {"cents": cents, "amount": plain_cents(cents)}


def period_json(period: Period) -> dict:
    return {"label": period.label, "start": period.start.isoformat(), "end": period.end.isoformat()}
