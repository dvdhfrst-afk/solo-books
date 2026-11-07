"""Reading exports through a mapping, and re-importing safely."""
from __future__ import annotations

from solo_books import importer, ledger
from solo_books.csvmap import Mapping, parse_export, suggest_mapping
from solo_books.errors import BooksError
from tests.helpers import BooksCase

BANK = (
    "Date,Description,Amount\n"
    "01/02/2025,COFFEE,-4.50\n"
    "01/03/2025,CLIENT PAYMENT,100.00\n"
)


class Mappings(BooksCase):
    def test_debit_credit_columns_and_preamble(self) -> None:
        path = self.write(
            "b.csv",
            "Example Bank\nExport 2025\n\nDate,Description,Debit,Credit\n"
            '01/02/2025,RENT,"1,000.00",\n01/03/2025,PAYOUT,,250.00\n',
        )
        mapping = Mapping(amount_column=None, debit_column="Debit", credit_column="Credit", skip_lines=2)
        parsed = parse_export(path, mapping)
        self.assertEqual([row.amount_cents for row in parsed.rows], [-100000, 25000])

    def test_debit_shown_negative_is_still_money_out(self) -> None:
        path = self.write("b.csv", "Date,Description,Debit,Credit\n01/02/2025,RENT,-50.00,\n")
        mapping = Mapping(amount_column=None, debit_column="Debit", credit_column="Credit")
        self.assertEqual(parse_export(path, mapping).rows[0].amount_cents, -5000)

    def test_invert_amount_for_cards_that_show_charges_positive(self) -> None:
        path = self.write("c.csv", "Date,Description,Amount\n01/02/2025,HOTEL,80.00\n01/03/2025,PAYMENT,-80.00\n")
        parsed = parse_export(path, Mapping(invert_amount=True))
        self.assertEqual([row.amount_cents for row in parsed.rows], [-8000, 8000])

    def test_columns_match_loosely_and_memo_reference_read(self) -> None:
        path = self.write(
            "c.csv", " date , DESCRIPTION,amount,Memo,Txn\n01/02/2025,HOTEL,-80.00,two nights,X1\n"
        )
        row = parse_export(path, Mapping(memo_column="memo", reference_column="txn")).rows[0]
        self.assertEqual((row.memo, row.reference), ("two nights", "X1"))

    def test_missing_column_names_what_it_found(self) -> None:
        path = self.write("c.csv", "When,What,How much\n")
        with self.assertRaises(BooksError) as caught:
            parse_export(path, Mapping())
        self.assertIn("'When'", str(caught.exception))

    def test_pending_footer_and_zero_rows_skip(self) -> None:
        path = self.write(
            "c.csv",
            "Date,Description,Amount,Status\n"
            "01/02/2025,HOTEL,-80.00,Posted\n"
            "01/03/2025,HOLD,-5.00,PENDING\n"
            "01/04/2025,NOTICE,0.00,Posted\n"
            "Ending balance,,,\n,,,\n",
        )
        parsed = parse_export(path, Mapping(status_column="Status", description_column="Description"))
        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(parsed.skipped, 3)
        self.assertEqual(parsed.errors, [])

    def test_row_missing_half_its_facts_is_an_error_not_a_footer(self) -> None:
        path = self.write("c.csv", "Date,Description,Amount\n01/02/2025,HOTEL,\n,HOTEL,-80.00\n")
        parsed = parse_export(path, Mapping())
        self.assertEqual(len(parsed.errors), 2)
        self.assertIn("date but no amount", parsed.errors[0])
        self.assertIn("amount but no date", parsed.errors[1])

    def test_footer_with_a_label_and_no_amount_skips_quietly(self) -> None:
        for footer in (",Total,", "Total,,", "End of statement,,"):
            path = self.write("d.csv", f"Date,Description,Amount\n01/02/2025,HOTEL,-80.00\n{footer}\n")
            parsed = parse_export(path, Mapping())
            self.assertEqual((len(parsed.rows), parsed.skipped, parsed.errors), (1, 1, []), footer)

    def test_byte_order_mark_and_windows_encoding(self) -> None:
        path = self.dir / "bom.csv"
        path.write_bytes(b"\xef\xbb\xbfDate,Description,Amount\r\n01/02/2025,CAFE,-1.00\r\n")
        self.assertEqual(parse_export(path, Mapping()).rows[0].description, "CAFE")
        path = self.dir / "latin.csv"
        path.write_bytes("Date,Description,Amount\n01/02/2025,CAFÉ,-1.00\n".encode("cp1252"))
        self.assertEqual(parse_export(path, Mapping()).rows[0].description, "CAFÉ")

    def test_mapping_rejects_contradictions(self) -> None:
        with self.assertRaises(BooksError):
            Mapping.from_dict({"amount_column": "A", "debit_column": "D", "credit_column": "C"})
        with self.assertRaises(BooksError):
            Mapping.from_dict({"amount_column": None})
        with self.assertRaises(BooksError):
            Mapping.from_dict({"colour": "red"})
        with self.assertRaises(BooksError):
            Mapping.from_dict({"skip_lines": "two"})
        self.assertIsNone(Mapping.from_dict({"debit_column": "D", "credit_column": "C"}).amount_column)

    def test_suggest_finds_header_columns_and_date_format(self) -> None:
        path = self.write(
            "s.csv",
            "Example Card activity\n\nTransaction Date,Post Date,Description,Amount,Status\n"
            "2025-01-05,2025-01-06,HOTEL,-80.00,Posted\n",
        )
        mapping, notes = suggest_mapping(path)
        self.assertEqual(mapping.skip_lines, 2)
        self.assertEqual(mapping.date_column, "Post Date")
        self.assertEqual(mapping.date_format, "%Y-%m-%d")
        self.assertEqual(mapping.status_column, "Status")
        self.assertEqual(len(parse_export(path, mapping).rows), 1)
        self.assertTrue(any("invert_amount" in note for note in notes))

    def test_suggest_debit_credit_and_reference(self) -> None:
        path = self.write(
            "s.csv", "Date,Payee,Withdrawals,Deposits,Transaction ID\n03/15/2025,RENT,10.00,,T1\n"
        )
        mapping, notes = suggest_mapping(path)
        self.assertIsNone(mapping.amount_column)
        self.assertEqual((mapping.debit_column, mapping.credit_column), ("Withdrawals", "Deposits"))
        self.assertEqual(mapping.reference_column, "Transaction ID")
        self.assertEqual(mapping.date_format, "%m/%d/%Y")
        self.assertTrue(notes)

    def test_suggest_flags_ambiguous_dates(self) -> None:
        path = self.write("s.csv", "Date,Description,Amount\n01/02/2025,A,-1.00\n")
        _, notes = suggest_mapping(path)
        self.assertTrue(any("month/day" in note for note in notes))

    def test_suggest_gives_up_plainly(self) -> None:
        path = self.write("s.csv", "x,y,z\n1,2,3\n")
        with self.assertRaises(BooksError):
            suggest_mapping(path)


class SafeReimport(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)

    def _count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def test_same_file_twice_adds_nothing_and_says_so(self) -> None:
        path = self.write("a.csv", BANK)
        first = importer.import_export(self.conn, self.acct, path)
        second = importer.import_export(self.conn, self.acct, path)
        self.assertEqual((first.rows_new, second.rows_new, second.rows_duplicate), (2, 0, 2))
        self.assertEqual(self._count(), 2)
        self.assertTrue(any("already imported" in w for w in second.warnings))

    def test_overlapping_exports(self) -> None:
        importer.import_export(self.conn, self.acct, self.write("a.csv", BANK))
        later = self.write("b.csv", BANK + "01/04/2025,RENT,-500.00\n")
        result = importer.import_export(self.conn, self.acct, later)
        self.assertEqual((result.rows_new, result.rows_duplicate), (1, 2))

    def test_two_real_purchases_on_one_day_both_stay_through_reimport(self) -> None:
        text = "Date,Description,Amount\n01/02/2025,COFFEE,-4.50\n01/02/2025,COFFEE,-4.50\n"
        first = importer.import_export(self.conn, self.acct, self.write("a.csv", text))
        self.assertEqual(first.rows_new, 2)
        self.assertTrue(any("repeat" in w for w in first.warnings))
        second = importer.import_export(self.conn, self.acct, self.write("b.csv", text))
        self.assertEqual((second.rows_new, second.rows_duplicate), (0, 2))

    def test_a_third_purchase_appearing_later_is_added(self) -> None:
        one = "Date,Description,Amount\n01/02/2025,COFFEE,-4.50\n"
        importer.import_export(self.conn, self.acct, self.write("a.csv", one))
        three = one + "01/02/2025,COFFEE,-4.50\n01/02/2025,COFFEE,-4.50\n"
        result = importer.import_export(self.conn, self.acct, self.write("b.csv", three))
        self.assertEqual((result.rows_new, result.rows_duplicate), (2, 1))
        self.assertEqual(self._count(), 3)

    def test_wording_case_and_spacing_do_not_make_a_new_row(self) -> None:
        importer.import_export(self.conn, self.acct, self.write("a.csv", "Date,Description,Amount\n01/02/2025,Coffee  Shop,-4.50\n"))
        result = importer.import_export(self.conn, self.acct, self.write("b.csv", "Date,Description,Amount\n01/02/2025,COFFEE SHOP,-4.50\n"))
        self.assertEqual(result.rows_new, 0)

    def test_same_row_in_two_accounts_is_two_rows(self) -> None:
        other = self.account(self.biz, "A Savings", "savings")
        path = self.write("a.csv", BANK)
        importer.import_export(self.conn, self.acct, path)
        result = importer.import_export(self.conn, other, path)
        self.assertEqual(result.rows_new, 2)

    def test_bank_row_id_wins_over_changed_text(self) -> None:
        acct = self.account(self.biz, "With Ids", mapping=Mapping(reference_column="Id"))
        before = "Date,Description,Amount,Id\n01/02/2025,PENDING COFFEE,-4.50,T1\n"
        after = "Date,Description,Amount,Id\n01/03/2025,COFFEE SHOP 44,-4.50,T1\n"
        importer.import_export(self.conn, acct, self.write("a.csv", before))
        self.assertEqual(importer.import_export(self.conn, acct, self.write("b.csv", after)).rows_new, 0)

    def test_changed_wording_without_an_id_is_flagged_not_guessed(self) -> None:
        before = "Date,Description,Amount\n01/02/2025,BLUE FERN CAFE 0102,-4.50\n"
        after = "Date,Description,Amount\n01/03/2025,BLUE FERN CAFE 0103,-4.50\n"
        importer.import_export(self.conn, self.acct, self.write("a.csv", before))
        result = importer.import_export(self.conn, self.acct, self.write("b.csv", after))
        self.assertEqual(result.rows_new, 1)
        self.assertTrue(any("Possible duplicate" in w for w in result.row_warnings))

    def test_bad_row_stops_the_whole_file(self) -> None:
        path = self.write("a.csv", BANK + "13/45/2025,BAD DATE,-1.00\n")
        with self.assertRaises(BooksError) as caught:
            importer.import_export(self.conn, self.acct, path)
        self.assertIn("Nothing was imported", str(caught.exception))
        self.assertIn("line 4", str(caught.exception))
        self.assertEqual(self._count(), 0)

    def test_skip_bad_keeps_the_readable_rows(self) -> None:
        path = self.write("a.csv", BANK + "01/05/2025,BAD AMOUNT,abc\n")
        result = importer.import_export(self.conn, self.acct, path, skip_bad=True)
        self.assertEqual((result.rows_new, result.rows_bad), (2, 1))

    def test_dry_run_saves_nothing_but_reports_everything(self) -> None:
        path = self.write("a.csv", BANK)
        result = importer.import_export(self.conn, self.acct, path, dry_run=True)
        self.assertEqual(result.rows_new, 2)
        self.assertEqual(self._count(), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0], 0)
        self.assertFalse(self.conn.in_transaction)

    def test_dry_run_shows_bad_rows_instead_of_stopping(self) -> None:
        path = self.write("a.csv", BANK + "01/05/2025,BAD AMOUNT,abc\n")
        result = importer.import_export(self.conn, self.acct, path, dry_run=True)
        self.assertEqual(result.rows_bad, 1)

    def test_backward_sign_on_a_card_is_flagged(self) -> None:
        card = self.account(self.biz, "A Card", "credit_card")
        text = "Date,Description,Amount\n01/02/2025,A,4.00\n01/03/2025,B,5.00\n01/04/2025,C,6.00\n"
        result = importer.import_export(self.conn, card, self.write("c.csv", text), dry_run=True)
        self.assertTrue(any("invert_amount" in w for w in result.warnings))

    def test_undo_then_reimport(self) -> None:
        path = self.write("a.csv", BANK)
        result = importer.import_export(self.conn, self.acct, path)
        outcome = importer.undo_import(self.conn, result.import_id)
        self.assertEqual(outcome["removed"], 2)
        self.assertEqual(self._count(), 0)
        again = importer.import_export(self.conn, self.acct, path)
        self.assertEqual(again.rows_new, 2)
        self.assertEqual(again.warnings, [])
        with self.assertRaises(BooksError):
            importer.undo_import(self.conn, result.import_id)

    def test_undo_protects_hand_work(self) -> None:
        result = importer.import_export(self.conn, self.acct, self.write("a.csv", BANK))
        row = self.conn.execute("SELECT id FROM transactions LIMIT 1").fetchone()
        ledger.categorize(self.conn, [row["id"]], self.category(self.biz, "6210"))
        with self.assertRaises(BooksError):
            importer.undo_import(self.conn, result.import_id)
        self.assertEqual(importer.undo_import(self.conn, result.import_id, force=True)["manual_lost"], 1)

    def test_deleted_row_stays_deleted_through_reimport(self) -> None:
        path = self.write("a.csv", BANK)
        importer.import_export(self.conn, self.acct, path)
        row = self.conn.execute("SELECT id FROM transactions WHERE description = 'COFFEE'").fetchone()
        ledger.delete_transaction(self.conn, row["id"])
        result = importer.import_export(self.conn, self.acct, path)
        self.assertEqual((result.rows_new, result.rows_deleted_before), (0, 1))
        self.assertEqual(self._count(), 1)

    def test_row_added_by_hand_is_not_doubled_by_a_later_export(self) -> None:
        from datetime import date

        ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "COFFEE")
        result = importer.import_export(self.conn, self.acct, self.write("a.csv", BANK))
        self.assertEqual((result.rows_new, result.rows_duplicate), (1, 1))

    def test_account_without_mapping_explains_itself(self) -> None:
        from solo_books import books

        bare = books.add_account(self.conn, self.biz, "Bare", "checking")
        with self.assertRaises(BooksError) as caught:
            importer.import_export(self.conn, bare, self.write("a.csv", BANK))
        self.assertIn("mapping", str(caught.exception))

    def test_stored_amounts_are_integers(self) -> None:
        importer.import_export(self.conn, self.acct, self.write("a.csv", BANK))
        kinds = {row[0] for row in self.conn.execute("SELECT typeof(amount_cents) FROM transactions")}
        self.assertEqual(kinds, {"integer"})
