"""Ready-made column mappings for the export layouts of common banks and card issuers.

Each entry in data/layouts.json is a Mapping plus the columns that tell it apart from the others. A layout
is a starting point, not a promise: banks change their downloads, so a dry run comes first.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .csvmap import Mapping, read_rows, read_text
from .errors import BooksError
from .money import parse_cents

LAYOUTS_FILE = Path(__file__).resolve().parent / "data" / "layouts.json"
_HEADER_SEARCH_ROWS = 30


@dataclass(frozen=True)
class Layout:
    id: str
    name: str
    kind: str
    mapping_fields: Tuple[Tuple[str, object], ...]
    signature: Tuple[str, ...]

    def mapping(self) -> Mapping:
        return Mapping.from_dict({key: list(value) if isinstance(value, tuple) else value for key, value in self.mapping_fields})


def load_layouts() -> Dict[str, Layout]:
    raw = json.loads(LAYOUTS_FILE.read_text(encoding="utf-8"))
    return {
        entry["id"]: Layout(
            entry["id"],
            entry["name"],
            entry["kind"],
            tuple((key, tuple(value) if isinstance(value, list) else value) for key, value in entry["mapping"].items()),
            tuple(name.lower() for name in entry.get("signature", ())),
        )
        for entry in raw["layouts"]
    }


def get_layout(layout_id: str) -> Layout:
    layouts = load_layouts()
    layout = layouts.get(layout_id.strip().lower())
    if layout is None:
        raise BooksError(f"no layout {layout_id!r}. See the list with 'mapping layouts'.")
    return layout


def _matches_headerless(layout: Layout, row: List[str]) -> bool:
    mapping = layout.mapping()
    names = [name.lower() for name in mapping.column_names or []]
    if len(row) != len(names):
        return False
    cells = dict(zip(names, (cell.strip() for cell in row)))
    try:
        datetime.strptime(cells[mapping.date_column.lower()], mapping.date_format)
        parse_cents(cells[(mapping.amount_column or "").lower()])
    except (BooksError, KeyError, ValueError):
        return False
    return True


def detect_layouts(path: Path) -> List[Layout]:
    """The layouts this file could be, most specific first. Layouts without a signature are never guessed."""
    text = read_text(path, None)
    rows = [row for _, row in read_rows(text, ",", Path(path).name)][:_HEADER_SEARCH_ROWS]
    found: List[Layout] = []
    for layout in load_layouts().values():
        if not layout.signature:
            continue
        if layout.signature == ("headerless",):
            if rows and _matches_headerless(layout, rows[0]):
                found.append(layout)
            continue
        for row in rows:
            names = {cell.strip().lower() for cell in row}
            if all(name in names for name in layout.signature):
                found.append(layout)
                break
    return sorted(found, key=lambda layout: -len(layout.signature))


def describe_match(path: Path) -> Optional[str]:
    """A hint for someone holding a file and no mapping. None when nothing fits or the file cannot be read."""
    try:
        found = detect_layouts(path)
    except BooksError:
        return None
    return found[0].id if found else None
