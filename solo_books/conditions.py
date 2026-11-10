"""What a rule can test and what it can do: the condition kinds and action kinds of the rule engine.

A rule holds a list of conditions, every one of which must match a row, and its actions. Each condition kind
knows how to check itself, describe itself, explain what a row showed it, and write itself to and read itself
from a rule pack. Adding a kind means adding it to CONDITION_KINDS and to the schema's list of kinds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Mapping, Optional, Pattern, Sequence

from .errors import BooksError
from .money import format_cents, parse_cents, plain_cents
from .periods import parse_iso_date
from .text import clean_cell, normalize_description

MAX_PATTERN = 300
MAX_NOTE = 500
# A repeat inside a repeat, such as (a+)+, can take years to fail on an ordinary line of text.
_NESTED_REPEAT = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d*,?\d*\})")


def check_regex(pattern: str) -> None:
    """Refuse a pattern that does not compile, or whose shape can hang a whole import."""
    if _NESTED_REPEAT.search(pattern):
        raise BooksError(
            f"regular expression {pattern!r} has a repeat inside a repeat, which can run for years on one line of "
            "text. Rewrite it without the outer repeat."
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise BooksError(f"bad regular expression {pattern!r}: {exc}") from None


@dataclass(frozen=True)
class Facts:
    """What a rule can see of one row."""

    account_id: int
    description: str
    memo: str
    merchant: str
    amount_cents: int
    posted_on: Optional[date] = None

    @property
    def text(self) -> str:
        return normalize_description(self.description)

    @property
    def memo_text(self) -> str:
        return normalize_description(self.memo) if self.memo else ""


@dataclass(frozen=True)
class Condition:
    """One test. Which fields it uses depends on its kind: text for wording and direction, low and high for
    amounts in cents, days of the month, and dates written as YYYYMMDD numbers, account_id for an account."""

    kind: str
    text: Optional[str] = None
    low: Optional[int] = None
    high: Optional[int] = None
    account_id: Optional[int] = None
    regex: Optional[Pattern[str]] = field(default=None, compare=False, repr=False)

    @property
    def group(self) -> str:
        return CONDITION_KINDS[self.kind].group

    def identity(self) -> tuple:
        """What makes two conditions the same test. Wording is compared the way rules compare it."""
        text = self.text
        if text is not None and self.kind != "regex":
            text = normalize_description(text)
        return (self.kind, text, self.low, self.high, self.account_id)

    def matches(self, facts: Facts) -> bool:
        return CONDITION_KINDS[self.kind].test(self, facts)

    def describe(self, account_names: Mapping[int, str]) -> str:
        return CONDITION_KINDS[self.kind].describe(self, account_names)


def _day_number(day: date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


def _day_text(number: int) -> str:
    return f"{number // 10000:04d}-{number // 100 % 100:02d}-{number % 100:02d}"


class ConditionKind:
    """The behavior every condition kind shares. Each kind below fills in its own tests and wording."""

    name = ""
    group = ""
    summary = ""
    # The keys a rule pack writes for this kind, besides "kind".
    fields: Sequence[str] = ()

    def test(self, condition: Condition, facts: Facts) -> bool:
        raise NotImplementedError

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        raise NotImplementedError

    def observed(self, facts: Facts, account_names: Mapping[int, str]) -> str:
        raise NotImplementedError

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        raise NotImplementedError

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        raise NotImplementedError


def _require_text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise BooksError(f"needs {key!r} as text")
    return value


def _optional_text(data: dict, key: str) -> Optional[str]:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BooksError(f"needs {key!r} as text")
    return value


def _amount(data: dict, key: str) -> int:
    """Amounts in a pack are text, such as "12.50", so no value is ever a rounded floating point number."""
    return parse_cents(_require_text(data, key))


class _TextKind(ConditionKind):
    group = "description"
    fields = ("text",)
    phrase = ""

    def make(self, pattern: str) -> Condition:
        pattern = clean_cell(pattern or "")
        if not pattern:
            raise BooksError("a rule needs a pattern")
        if len(pattern) > MAX_PATTERN:
            raise BooksError(f"a rule's pattern can be at most {MAX_PATTERN} characters")
        regex = None
        if self.name == "regex":
            check_regex(pattern)
            regex = re.compile(pattern, re.IGNORECASE)
        return Condition(self.name, text=pattern, regex=regex)

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        return f"{self.phrase} '{condition.text}'"

    def observed(self, facts: Facts, account_names: Mapping[int, str]) -> str:
        # The wording is shown once, above every rule, rather than under each one.
        return ""

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        return {"kind": self.name, "text": condition.text}

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        return self.make(_require_text(data, "text"))


class Contains(_TextKind):
    name = "contains"
    phrase = "contains"
    summary = "the description, the merchant name or the memo contains this wording"

    def test(self, condition: Condition, facts: Facts) -> bool:
        # A merchant name can skip a store number that sits between its words, so check it too.
        wanted = normalize_description(condition.text or "")
        return wanted in facts.text or wanted in facts.merchant or (bool(facts.memo_text) and wanted in facts.memo_text)


class StartsWith(_TextKind):
    name = "starts_with"
    phrase = "starts with"
    summary = "the description starts with this wording"

    def test(self, condition: Condition, facts: Facts) -> bool:
        return facts.text.startswith(normalize_description(condition.text or ""))


class Exact(_TextKind):
    name = "exact"
    phrase = "is exactly"
    summary = "the whole description, or the merchant name, is exactly this wording"

    def test(self, condition: Condition, facts: Facts) -> bool:
        wanted = normalize_description(condition.text or "")
        return facts.text == wanted or facts.merchant == wanted


class Regex(_TextKind):
    name = "regex"
    phrase = "matches the regular expression"
    summary = "a regular expression finds a match in the description or the memo"

    def test(self, condition: Condition, facts: Facts) -> bool:
        regex = condition.regex or re.compile(condition.text or "", re.IGNORECASE)
        return bool(regex.search(facts.text) or (facts.memo_text and regex.search(facts.memo_text)))


class _AmountKind(ConditionKind):
    """Amounts are sizes. 12.00 out and 12.00 in are both 12.00; a direction condition tells them apart."""

    group = "amount"

    def observed(self, facts: Facts, account_names: Mapping[int, str]) -> str:
        return f"the size is {format_cents(abs(facts.amount_cents))}"


class AmountEquals(_AmountKind):
    name = "amount_equals"
    summary = "the size of the amount is exactly this much"
    fields = ("amount",)

    def test(self, condition: Condition, facts: Facts) -> bool:
        return abs(facts.amount_cents) == condition.low

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        return f"size is {format_cents(condition.low or 0)}"

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        return {"kind": self.name, "amount": plain_cents(condition.low or 0)}

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        return amount_condition(_amount(data, "amount"), _amount(data, "amount"))


class AmountAtLeast(_AmountKind):
    name = "amount_at_least"
    summary = "the size of the amount is this much or more"
    fields = ("amount",)

    def test(self, condition: Condition, facts: Facts) -> bool:
        return abs(facts.amount_cents) >= (condition.low or 0)

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        return f"size is at least {format_cents(condition.low or 0)}"

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        return {"kind": self.name, "amount": plain_cents(condition.low or 0)}

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        return amount_condition(_amount(data, "amount"), None)


class AmountAtMost(_AmountKind):
    name = "amount_at_most"
    summary = "the size of the amount is this much or less"
    fields = ("amount",)

    def test(self, condition: Condition, facts: Facts) -> bool:
        return abs(facts.amount_cents) <= (condition.high or 0)

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        return f"size is at most {format_cents(condition.high or 0)}"

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        return {"kind": self.name, "amount": plain_cents(condition.high or 0)}

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        return amount_condition(None, _amount(data, "amount"))


class AmountBetween(_AmountKind):
    name = "amount_between"
    summary = "the size of the amount is from the first figure to the second, both included"
    fields = ("min", "max")

    def test(self, condition: Condition, facts: Facts) -> bool:
        return (condition.low or 0) <= abs(facts.amount_cents) <= (condition.high or 0)

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        return f"size is from {format_cents(condition.low or 0)} to {format_cents(condition.high or 0)}"

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        return {"kind": self.name, "min": plain_cents(condition.low or 0), "max": plain_cents(condition.high or 0)}

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        low, high = _amount(data, "min"), _amount(data, "max")
        if low == high:
            raise BooksError("amount_between needs two different figures; use amount_equals for one")
        return amount_condition(low, high)


def amount_condition(low: Optional[int], high: Optional[int]) -> Condition:
    """The one amount condition that says 'at least low and at most high', whichever of them is given."""
    for value in (low, high):
        if value is not None and value < 0:
            raise BooksError("amount limits are sizes, so give them without a minus sign. Use a direction for money in or out.")
    if low is not None and high is not None:
        if low > high:
            raise BooksError("minimum amount is above the maximum")
        return Condition("amount_equals" if low == high else "amount_between", low=low, high=high)
    if low is not None:
        return Condition("amount_at_least", low=low)
    if high is not None:
        return Condition("amount_at_most", high=high)
    raise BooksError("an amount condition needs a minimum, a maximum or both")


class Direction(ConditionKind):
    name = "direction"
    group = "direction"
    summary = "money comes in (a positive amount) or goes out (a negative one)"
    fields = ("direction",)

    def make(self, direction: str) -> Condition:
        direction = (direction or "").strip().lower()
        if direction not in ("in", "out"):
            raise BooksError("direction must be in or out")
        return Condition(self.name, text=direction)

    def test(self, condition: Condition, facts: Facts) -> bool:
        return facts.amount_cents > 0 if condition.text == "in" else facts.amount_cents < 0

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        return f"money {condition.text}"

    def observed(self, facts: Facts, account_names: Mapping[int, str]) -> str:
        if facts.amount_cents == 0:
            return "the amount is zero"
        return "money comes in" if facts.amount_cents > 0 else "money goes out"

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        return {"kind": self.name, "direction": condition.text}

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        return self.make(_require_text(data, "direction"))


class Account(ConditionKind):
    name = "account"
    group = "account"
    summary = "the row is in this account"
    fields = ("account",)

    def make(self, account_id: int) -> Condition:
        return Condition(self.name, account_id=account_id)

    def test(self, condition: Condition, facts: Facts) -> bool:
        # An account lost in a salvage leaves no account to match, so the rule stays out of the way.
        return condition.account_id is not None and condition.account_id == facts.account_id

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        if condition.account_id is None:
            return "account is one that no longer exists"
        return f"account is {account_names.get(condition.account_id, f'account {condition.account_id}')}"

    def observed(self, facts: Facts, account_names: Mapping[int, str]) -> str:
        if facts.account_id not in account_names:
            return "no account was given"
        return f"the account is {account_names[facts.account_id]}"

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        if condition.account_id is None or condition.account_id not in account_names:
            raise BooksError("a rule names an account that no longer exists. Delete or edit that rule first.")
        return {"kind": self.name, "account": account_names[condition.account_id]}

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        return self.make(find_account(_require_text(data, "account")))


class DateBetween(ConditionKind):
    name = "date_between"
    group = "date"
    summary = "the row's date is on or after the first date and on or before the second; either may be left out"
    fields = ("from", "to")

    def make(self, start: Optional[date], end: Optional[date]) -> Condition:
        if start is None and end is None:
            raise BooksError("a date condition needs a first date, a last date or both")
        if start is not None and end is not None and start > end:
            raise BooksError("the first date of the rule comes after its last date")
        return Condition(
            self.name,
            low=_day_number(start) if start else None,
            high=_day_number(end) if end else None,
        )

    def test(self, condition: Condition, facts: Facts) -> bool:
        if facts.posted_on is None:
            return False
        day = _day_number(facts.posted_on)
        return (condition.low is None or day >= condition.low) and (condition.high is None or day <= condition.high)

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        if condition.low is not None and condition.high is not None:
            return f"date from {_day_text(condition.low)} to {_day_text(condition.high)}"
        if condition.low is not None:
            return f"date on or after {_day_text(condition.low)}"
        return f"date on or before {_day_text(condition.high or 0)}"

    def observed(self, facts: Facts, account_names: Mapping[int, str]) -> str:
        return f"the date is {facts.posted_on.isoformat()}" if facts.posted_on else "no date was given"

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        data: Dict[str, object] = {"kind": self.name}
        if condition.low is not None:
            data["from"] = _day_text(condition.low)
        if condition.high is not None:
            data["to"] = _day_text(condition.high)
        return data

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        start, end = _optional_text(data, "from"), _optional_text(data, "to")
        return self.make(parse_iso_date(start) if start else None, parse_iso_date(end) if end else None)


class DayOfMonth(ConditionKind):
    name = "day_of_month_between"
    group = "day"
    summary = (
        "the day of the month is from the first number to the second; 25 to 5 wraps past the end of the month"
    )
    fields = ("from", "to")

    def make(self, first: Optional[int], last: Optional[int]) -> Condition:
        if first is None and last is None:
            raise BooksError("a day-of-month condition needs a first day, a last day or both")
        first = 1 if first is None else first
        last = 31 if last is None else last
        for value in (first, last):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 31:
                raise BooksError(f"a day of the month is a whole number from 1 to 31, not {value!r}")
        return Condition(self.name, low=first, high=last)

    def test(self, condition: Condition, facts: Facts) -> bool:
        if facts.posted_on is None:
            return False
        day, first, last = facts.posted_on.day, condition.low or 1, condition.high or 31
        if first <= last:
            return first <= day <= last
        return day >= first or day <= last

    def describe(self, condition: Condition, account_names: Mapping[int, str]) -> str:
        return f"day of month from {condition.low} to {condition.high}"

    def observed(self, facts: Facts, account_names: Mapping[int, str]) -> str:
        return f"the day of the month is {facts.posted_on.day}" if facts.posted_on else "no date was given"

    def to_json(self, condition: Condition, account_names: Mapping[int, str]) -> dict:
        return {"kind": self.name, "from": condition.low, "to": condition.high}

    def from_json(self, data: dict, find_account: Callable[[str], int]) -> Condition:
        return self.make(data.get("from"), data.get("to"))


_TEXT = {kind.name: kind for kind in (Contains(), StartsWith(), Exact(), Regex())}
_DIRECTION = Direction()
_ACCOUNT = Account()
_DATE = DateBetween()
_DAY = DayOfMonth()
CONDITION_KINDS: Dict[str, ConditionKind] = {
    **_TEXT,
    **{kind.name: kind for kind in (AmountEquals(), AmountAtLeast(), AmountAtMost(), AmountBetween())},
    **{kind.name: kind for kind in (_DIRECTION, _ACCOUNT, _DATE, _DAY)},
}
TEXT_KINDS = ("contains", "starts_with", "exact", "regex")
# The order conditions are shown and tried in. A rule holds at most one condition from each group.
GROUPS = ("description", "amount", "direction", "account", "date", "day")
# The names schema 2 and the rule CSV use for the four kinds of wording.
CSV_MATCH_NAMES = {"contains": "contains", "starts": "starts_with", "exact": "exact", "regex": "regex"}


def text_condition(match_type: str, pattern: str) -> Condition:
    """A wording condition from the names the rule CSV and 'rules add' have always used."""
    kind = CSV_MATCH_NAMES.get(match_type, match_type)
    if kind not in TEXT_KINDS:
        raise BooksError(f"match type must be one of: {', '.join(CSV_MATCH_NAMES)}")
    return _TEXT[kind].make(pattern)


def check_conditions(conditions: Sequence[Condition]) -> List[Condition]:
    """A rule's conditions in their standard order, refusing an empty list and two from one group."""
    if not conditions:
        raise BooksError("a rule needs at least one condition, such as --contains or --amount-min")
    seen: Dict[str, Condition] = {}
    for condition in conditions:
        if condition.kind not in CONDITION_KINDS:
            raise BooksError(f"unknown condition kind {condition.kind!r}")
        if condition.group in seen:
            raise BooksError(f"a rule can have only one {condition.group} condition")
        seen[condition.group] = condition
    return [seen[group] for group in GROUPS if group in seen]


def condition_from_json(data: object, find_account: Callable[[str], int]) -> Condition:
    if not isinstance(data, dict):
        raise BooksError("each condition must be a JSON object")
    kind_name = data.get("kind")
    if kind_name not in CONDITION_KINDS:
        raise BooksError(f"unknown condition kind {kind_name!r}")
    kind = CONDITION_KINDS[kind_name]
    extra = set(data) - {"kind", *kind.fields}
    if extra:
        raise BooksError(f"a {kind_name} condition has unknown key(s): {', '.join(sorted(extra))}")
    try:
        return kind.from_json(data, find_account)
    except BooksError as exc:
        raise BooksError(f"{kind_name}: {exc}") from None


def describe_all(conditions: Sequence[Condition], account_names: Mapping[int, str]) -> str:
    if not conditions:
        return "no conditions (never matches)"
    return " and ".join(condition.describe(account_names) for condition in conditions)


# Actions. Every rule books a category; it can also put a note on the rows it books.

ACTION_KINDS = {
    "book_category": "book the row to this category (every rule has exactly one)",
    "set_note": "put this note on the row, unless the row already has a note someone wrote",
}


def clean_note(note: Optional[str]) -> str:
    note = clean_cell(note or "")
    if len(note) > MAX_NOTE:
        raise BooksError(f"a rule's note can be at most {MAX_NOTE} characters")
    return note


def first_and_last_day(start: Optional[str], end: Optional[str]) -> Condition:
    """A date condition from the --from and --to flags."""
    return _DATE.make(parse_iso_date(start) if start else None, parse_iso_date(end) if end else None)


def day_range(first: Optional[int], last: Optional[int]) -> Condition:
    return _DAY.make(first, last)


def direction_condition(direction: str) -> Condition:
    return _DIRECTION.make(direction)


def account_condition(account_id: int) -> Condition:
    return _ACCOUNT.make(account_id)


def explain_condition(condition: Condition, facts: Facts, account_names: Mapping[int, str]) -> str:
    """One line for 'rules explain': the test, whether it held, and what the row showed it."""
    kind = CONDITION_KINDS[condition.kind]
    verdict = "yes" if condition.matches(facts) else "no"
    seen = kind.observed(facts, account_names)
    return f"{condition.describe(account_names)}: {verdict}" + (f" ({seen})" if seen else "")


def compile_stored(kind: str, text: Optional[str]) -> Optional[Pattern[str]]:
    """The compiled form of a stored regular expression, or None for any other kind."""
    if kind != "regex":
        return None
    return re.compile(text or "", re.IGNORECASE)
