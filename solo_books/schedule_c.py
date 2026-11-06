"""Schedule C line definitions, read from one stamped data file per tax year."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from .errors import BooksError

DATA_DIR = Path(__file__).resolve().parent / "data"


class Form:
    """One tax year's lines. Lines are looked up by number or by stable key."""

    def __init__(self, raw: dict) -> None:
        self.tax_year: int = raw["tax_year"]
        self.form: str = raw["form"]
        self.source_url: str = raw["source_url"]
        self.retrieved: str = raw["retrieved"]
        self.revision: str = raw["form_revision"]
        self.lines: List[dict] = raw["lines"]
        self._by_line: Dict[str, dict] = {entry["line"]: entry for entry in self.lines}
        self._by_key: Dict[str, dict] = {}
        for entry in self.lines:
            if "key" in entry:
                self._by_key[entry["key"]] = entry
            for other in entry.get("also_accepts_keys", []):
                self._by_key[other] = entry

    def find_by_line(self, line: str) -> Optional[dict]:
        return self._by_line.get(line.strip().lower())

    def find_by_key(self, key: str) -> Optional[dict]:
        return self._by_key.get(key.strip().lower())

    def stamp(self) -> str:
        return f"{self.form}, tax year {self.tax_year}, {self.revision}, {self.source_url}, retrieved {self.retrieved}"


def line_label(entry: dict) -> str:
    """The name as a reader needs it: sub-lines carry their group, as they do on the form."""
    if "group" in entry:
        return f"{entry['group']}: {entry['name']}"
    return entry["name"]


def available_years() -> List[int]:
    years = []
    for path in DATA_DIR.glob("schedule_c_*.json"):
        match = re.match(r"schedule_c_(\d{4})\.json$", path.name)
        if match:
            years.append(int(match.group(1)))
    return sorted(years)


def load_form(year: int) -> Form:
    path = DATA_DIR / f"schedule_c_{year}.json"
    if not path.exists():
        have = ", ".join(str(y) for y in available_years())
        raise BooksError(
            f"no Schedule C line data for tax year {year} (have: {have}). "
            f"Add solo_books/data/schedule_c_{year}.json from the IRS form, or pass --lines-year "
            "to use another year's lines knowingly."
        )
    return Form(json.loads(path.read_text(encoding="utf-8")))


def latest_form() -> Form:
    return load_form(available_years()[-1])
