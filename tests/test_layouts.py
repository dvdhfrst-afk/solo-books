"""Every ready-made layout against an invented file written that layout's way, and OFX downloads."""
from __future__ import annotations

import json

from solo_books import importer, layouts
from solo_books.csvmap import Mapping, parse_export
from solo_books.errors import BooksError
from solo_books.money import parse_cents
from solo_books.ofx import looks_like_ofx, parse_ofx
from tests.helpers import REPO_ROOT, BooksCase

LAYOUT_FILES = REPO_ROOT / "samples" / "layouts"
EXPECTED = json.loads((LAYOUT_FILES / "expected.json").read_text(encoding="utf-8"))


class Layouts(BooksCase):
    def test_every_layout_reads_its_sample_to_the_same_rows(self) -> None:
        all_layouts = layouts.load_layouts()
        self.assertGreaterEqual(len(all_layouts), 20)
        for layout in all_layouts.values():
            path = LAYOUT_FILES / f"example-{layout.id}.csv"
            parsed = parse_export(path, layout.mapping())
            self.assertEqual(parsed.errors, [], layout.id)
            got = [{"date": row.posted_on.isoformat(), "cents": row.amount_cents} for row in parsed.rows]
            self.assertEqual(got, EXPECTED["rows"], layout.id)

    def test_every_layout_with_a_signature_is_recognized_from_its_sample(self) -> None:
        for layout in layouts.load_layouts().values():
            if layout.signature:
                found = layouts.detect_layouts(LAYOUT_FILES / f"example-{layout.id}.csv")
                self.assertEqual(found[0].id, layout.id, layout.id)

    def test_pending_rows_stay_out_where_a_layout_marks_them(self) -> None:
        for layout_id in ("citi-card", "usaa", "schwab-bank", "mercury"):
            parsed = parse_export(LAYOUT_FILES / f"example-{layout_id}.csv", layouts.get_layout(layout_id).mapping())
            self.assertEqual(parsed.skipped, 1, layout_id)

    def test_import_through_the_command_line_with_a_layout(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "A Card", "--kind", "credit_card", "--layout", "discover-card")
        out = self.ok("import", "A Card", str(LAYOUT_FILES / "example-discover-card.csv"))
        self.assertIn("new:                5", out)
        rows = self.conn.execute("SELECT SUM(amount_cents) FROM transactions").fetchone()[0]
        self.assertEqual(rows, EXPECTED["net_cents"])
        self.assertNotIn("unusual for a card", out)

    def test_a_flag_can_adjust_a_layout(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "A Card", "--kind", "credit_card", "--layout", "discover-card", "--date-col", "Trans. Date")
        self.assertIn("Trans. Date", self.ok("account", "show", "A Card"))

    def test_layout_and_mapping_cannot_both_be_given(self) -> None:
        self.ok("business", "add", "Business A")
        mapping = self.write("m.json", "{}")
        self.assertIn(
            "not both",
            self.fails("account", "add", "X", "--kind", "checking", "--layout", "ally", "--mapping", str(mapping)),
        )

    def test_unknown_layout_says_where_to_look(self) -> None:
        self.ok("business", "add", "Business A")
        self.assertIn("mapping layouts", self.fails("account", "add", "X", "--kind", "checking", "--layout", "nope"))

    def test_layouts_are_listed(self) -> None:
        code, out, _ = self.run_cli("mapping", "layouts")
        self.assertEqual(code, 0)
        self.assertIn("chase-card", out)
        self.assertIn("checked against a live export", out)

    def test_suggest_names_the_layout(self) -> None:
        code, out, err = self.run_cli("mapping", "suggest", str(LAYOUT_FILES / "example-apple-card.csv"))
        self.assertEqual(code, 0)
        self.assertIn("apple-card", err)
        self.assertTrue(json.loads(out)["invert_amount"])

    def test_import_without_a_mapping_names_the_layout_that_fits(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "A Bank", "--kind", "checking")
        message = self.fails("import", "A Bank", str(LAYOUT_FILES / "example-pnc.csv"))
        self.assertIn("--layout pnc", message)

    def test_a_plain_file_is_never_guessed_to_be_a_layout_with_a_flipped_sign(self) -> None:
        self.assertEqual(layouts.detect_layouts(LAYOUT_FILES / "example-generic-amount.csv"), [])

    def test_header_is_found_however_many_lines_sit_above_it(self) -> None:
        mapping = layouts.get_layout("barclays-card").mapping()
        for extra in ("", "One more line\n", "One\nTwo\nThree\nFour\n"):
            path = self.write(
                "x.csv",
                f"{extra}Transaction Date,Description,Category,Amount\n03/03/2025,CAFE,Other,-1.00\n",
            )
            self.assertEqual(len(parse_export(path, mapping).rows), 1, repr(extra))

    def test_direction_column_with_an_unknown_word_is_a_bad_row(self) -> None:
        path = self.write("x.csv", "Date,Description,Amount,Dir\n03/03/2025,CAFE,1.00,Sideways\n")
        parsed = parse_export(path, Mapping(direction_column="Dir"))
        self.assertIn("Sideways", parsed.errors[0])

    def test_direction_column_overrides_any_printed_sign(self) -> None:
        path = self.write("x.csv", "Date,Description,Amount,Dir\n03/03/2025,CAFE,-1.00,Credit\n03/04/2025,CAFE,1.00,DR\n")
        parsed = parse_export(path, Mapping(direction_column="Dir"))
        self.assertEqual([row.amount_cents for row in parsed.rows], [100, -100])

    def test_no_header_mapping_keeps_the_first_row(self) -> None:
        path = self.write("x.csv", '"03/03/2025","-1.00","*","","CAFE"\n')
        parsed = parse_export(path, layouts.get_layout("wells-fargo").mapping())
        self.assertEqual(len(parsed.rows), 1)


class Ofx(BooksCase):
    QFX = LAYOUT_FILES / "example-bank.qfx"

    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "A Bank", "--kind", "checking")

    def wrap(self, body: str, extra: str = "") -> str:
        return f"OFXHEADER:100\n\n<OFX><STMTRS>{extra}<BANKTRANLIST>{body}</BANKTRANLIST></STMTRS></OFX>"

    def test_import_needs_no_mapping(self) -> None:
        out = self.ok("import", "A Bank", str(self.QFX))
        self.assertIn("new:                5", out)
        total = self.conn.execute("SELECT SUM(amount_cents) FROM transactions").fetchone()[0]
        self.assertEqual(total, EXPECTED["net_cents"])

    def test_importing_again_adds_nothing_because_of_the_bank_ids(self) -> None:
        self.ok("import", "A Bank", str(self.QFX))
        out = self.ok("import", "A Bank", str(self.QFX))
        self.assertIn("new:                0", out)
        self.assertIn("already in books:   5", out)

    def test_xml_form_and_escapes(self) -> None:
        text = (
            '<?xml version="1.0"?><?OFX OFXHEADER="200"?><OFX><STMTTRN><DTPOSTED>20250303</DTPOSTED>'
            "<TRNAMT>-1.50</TRNAMT><FITID>A1</FITID><NAME>SMITH &amp; SONS</NAME><MEMO>BOOTH 4</MEMO></STMTTRN></OFX>"
        )
        self.assertTrue(looks_like_ofx(text))
        row = parse_ofx(text, "x").rows[0]
        self.assertEqual((row.description, row.memo, row.amount_cents, row.reference), ("SMITH & SONS", "BOOTH 4", -150, "A1"))

    def test_an_entity_declaration_is_inert(self) -> None:
        text = (
            '<?xml version="1.0"?><!DOCTYPE OFX [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
            "<OFX><STMTTRN><DTPOSTED>20250303<TRNAMT>-1.50<FITID>A<NAME>&x;</STMTTRN></OFX>"
        )
        self.assertEqual(parse_ofx(text, "x").rows[0].description, "&x;")

    def test_a_bad_row_is_reported_with_its_line(self) -> None:
        text = self.wrap("<STMTTRN>\n<DTPOSTED>2025\n<TRNAMT>1.00\n<NAME>A\n</STMTTRN>\n<STMTTRN>\n<DTPOSTED>20250303\n<TRNAMT>abc\n<NAME>B\n</STMTTRN>")
        parsed = parse_ofx(text, "x")
        self.assertEqual(len(parsed.errors), 2)
        self.assertIn("DTPOSTED", parsed.errors[0])
        self.assertIn("not an amount", parsed.errors[1])

    def test_two_accounts_in_one_file_stop(self) -> None:
        text = self.wrap("", "<ACCTID>1<ACCTID>2")
        with self.assertRaises(BooksError):
            parse_ofx(text, "x")

    def test_another_currency_stops_rather_than_reading_as_dollars(self) -> None:
        text = self.wrap("<STMTTRN><DTPOSTED>20250303<TRNAMT>1.00<NAME>A</STMTTRN>", "<CURDEF>EUR")
        with self.assertRaises(BooksError):
            parse_ofx(text, "x")

    def test_statement_comparison_reads_ofx_too(self) -> None:
        self.ok("import", "A Bank", str(self.QFX))
        out = self.ok(
            "reconcile", "run", "A Bank", "--from", "2025-03-01", "--to", "2025-03-31",
            "--begin", "1000.00", "--end", "1388.51", "--statement", str(self.QFX),
        )
        self.assertIn("Every line matches", out)
        self.assertIn("ties to the cent", out)

    def test_changing_nothing_in_how_a_file_is_read_stays_quiet(self) -> None:
        account = self.conn.execute("SELECT a.*, 'x' AS business_name FROM accounts a").fetchone()
        result = importer.import_export(self.conn, account, self.QFX)
        self.assertEqual(result.rows_new, 5)


class MoneyForms(BooksCase):
    def test_more_ways_banks_write_amounts(self) -> None:
        for text, cents in (
            ("12.34 DR", -1234),
            ("12.34 CR", 1234),
            ("12.34CR", 1234),
            ("\u221212.34", -1234),
            ("\u201312.34", -1234),
            ("$ 1,234.50", 123450),
        ):
            self.assertEqual(parse_cents(text), cents, text)

    def test_ways_that_stay_refused_rather_than_guessed(self) -> None:
        for text in ("1.234,56", "1,23", "12,34", "1e5", "NaN", "12.34 EUR", "9" * 30):
            with self.assertRaises(BooksError, msg=text):
                parse_cents(text)
