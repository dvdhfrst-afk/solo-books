"""Small pieces: money, periods, text cleanup."""
from __future__ import annotations

import unittest
from datetime import date

from solo_books.errors import BooksError
from solo_books.money import format_cents, parse_cents, plain_cents
from solo_books.periods import parse_iso_date, parse_period, split_period
from solo_books.text import merchant_key, normalize_description, slug, spreadsheet_safe


class Money(unittest.TestCase):
    def test_forms_banks_write(self) -> None:
        cases = {
            "12.34": 1234,
            "-12.34": -1234,
            "$1,234.56": 123456,
            "-$5": -500,
            "$-5.00": -500,
            "(12.34)": -1234,
            "($1,000.00)": -100000,
            "12.34-": -1234,
            "+3": 300,
            ".5": 50,
            "7.": 700,
            " 0.07 ": 7,
            "1,000,000.01": 100000001,
            "10.100": 1010,
        }
        for text, cents in cases.items():
            self.assertEqual(parse_cents(text), cents, text)

    def test_refuses_what_it_cannot_read_exactly(self) -> None:
        for text in ("", "abc", "1.005", "1,23.00", "12,34", "1.2.3", "$", "--5", "."):
            with self.assertRaises(BooksError, msg=text):
                parse_cents(text)

    def test_formats(self) -> None:
        self.assertEqual(format_cents(123456789), "1,234,567.89")
        self.assertEqual(format_cents(-5), "-0.05")
        self.assertEqual(format_cents(0), "0.00")
        self.assertEqual(plain_cents(123456789), "1234567.89")
        self.assertEqual(plain_cents(-100), "-1.00")

    def test_round_trip_every_cent_value_near_float_trouble(self) -> None:
        for cents in list(range(-300, 300)) + [1999, 2999, 110, 1001, 10000001]:
            self.assertEqual(parse_cents(plain_cents(cents)), cents)


class Periods(unittest.TestCase):
    def test_named_periods(self) -> None:
        self.assertEqual(parse_period("2024").end, date(2024, 12, 31))
        q2 = parse_period("2025-Q2")
        self.assertEqual((q2.start, q2.end), (date(2025, 4, 1), date(2025, 6, 30)))
        leap = parse_period("2024-02")
        self.assertEqual(leap.end, date(2024, 2, 29))
        span = parse_period("2025-01-15..2025-02-20")
        self.assertEqual((span.start, span.end), (date(2025, 1, 15), date(2025, 2, 20)))

    def test_bad_periods(self) -> None:
        for text in ("25", "2025-13", "2025-Q5", "2025-02-20..2025-01-15", "soon", "2025-02-30..2025-03-01"):
            with self.assertRaises(BooksError, msg=text):
                parse_period(text)

    def test_iso_date_is_strict(self) -> None:
        with self.assertRaises(BooksError):
            parse_iso_date("1/2/2025")
        self.assertEqual(parse_iso_date("2025-01-02"), date(2025, 1, 2))

    def test_split_by_quarter_and_month(self) -> None:
        year = parse_period("2025")
        self.assertEqual([b.label for b in split_period(year, "quarter")], ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"])
        months = split_period(year, "month")
        self.assertEqual(len(months), 12)
        self.assertEqual(months[1].end, date(2025, 2, 28))
        self.assertEqual(sum((b.end - b.start).days + 1 for b in months), 365)

    def test_split_clips_partial_buckets_and_says_so(self) -> None:
        buckets = split_period(parse_period("2025-02-15..2025-04-10"), "month")
        self.assertEqual(
            [b.label for b in buckets],
            ["2025-02-15..2025-02-28", "2025-03", "2025-04-01..2025-04-10"],
        )

    def test_split_across_years(self) -> None:
        buckets = split_period(parse_period("2024-11-01..2025-02-28"), "quarter")
        self.assertEqual(buckets[0].label, "2024-11-01..2024-12-31")
        self.assertEqual(buckets[1].label, "2025-01-01..2025-02-28")
        self.assertEqual(len(split_period(parse_period("2024-06-01..2025-06-30"), "year")), 2)


class Text(unittest.TestCase):
    def test_merchant_key_groups_one_payee(self) -> None:
        first = merchant_key("SQ *BLUE FERN CAFE 0423 OGDEN UT")
        second = merchant_key("sq *blue  fern cafe 0511 ogden ut")
        self.assertEqual(first, "BLUE FERN CAFE")
        self.assertEqual(first, second)

    def test_merchant_key_strips_wrappers(self) -> None:
        self.assertEqual(merchant_key("POS DEBIT STAPLE HUT 8812"), "STAPLE HUT")
        self.assertEqual(merchant_key("TST* CORNER DELI 442"), "CORNER DELI")
        self.assertEqual(merchant_key("PURCHASE AUTHORIZED ON 03/12 NEON ADS"), "NEON ADS")

    def test_merchant_key_never_empty(self) -> None:
        self.assertEqual(merchant_key("12345"), "12345")
        self.assertEqual(merchant_key("ACH"), "ACH")

    def test_normalize(self) -> None:
        self.assertEqual(normalize_description("  a  b\tc "), "A B C")

    def test_spreadsheet_safe(self) -> None:
        self.assertEqual(spreadsheet_safe("=SUM(A1)"), "'=SUM(A1)")
        self.assertEqual(spreadsheet_safe("@cmd"), "'@cmd")
        self.assertEqual(spreadsheet_safe("NORMAL"), "NORMAL")

    def test_slug(self) -> None:
        self.assertEqual(slug("Business A, LLC"), "business-a-llc")
        self.assertEqual(slug("!!!"), "business")


if __name__ == "__main__":
    unittest.main()
