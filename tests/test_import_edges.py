"""Exports that fight back: summary lines, reused ids, unquoted amounts, changed mappings."""
from __future__ import annotations

from datetime import date

from solo_books import importer, ledger, reconcile
from solo_books.csvmap import Mapping, parse_export, suggest_mapping
from solo_books.errors import BooksError
from tests.helpers import BooksCase

WITH_IDS = Mapping(reference_column="Ref")


class SummaryLines(BooksCase):
    def _rows(self, *lines: str, mapping: Mapping = None):
        path = self.write("x.csv", "\n".join(lines) + "\n")
        return parse_export(path, mapping or Mapping())

    def test_summary_line_with_figures_is_skipped(self) -> None:
        parsed = self._rows(
            "Date,Description,Amount",
            "01/02/2026,HOTEL,-80.00",
            "Total,,5000.00",
            ",Statement total,1311.16",
            "Ending balance,,4200.00",
        )
        self.assertEqual((len(parsed.rows), parsed.skipped, parsed.errors), (1, 3, []))

    def test_summary_line_with_debit_and_credit_figures_is_skipped(self) -> None:
        parsed = self._rows(
            "Date,Description,Debit,Credit",
            "01/02/2026,HOTEL,80.00,",
            'Total,,"$6,171.87","$9,676.18",,',
            mapping=Mapping(amount_column=None, debit_column="Debit", credit_column="Credit"),
        )
        self.assertEqual((len(parsed.rows), parsed.skipped, parsed.errors), (1, 1, []))

    def test_dated_balance_lines_do_not_become_money(self) -> None:
        parsed = self._rows(
            "Date,Description,Amount",
            "01/31/2026,TOTAL,5000.00",
            "01/31/2026,Ending balance,5000.00",
            "01/31/2026,Beginning  Balance,100.00",
        )
        self.assertEqual((parsed.rows, parsed.skipped), ([], 3))

    def test_a_merchant_that_starts_with_such_a_word_stays_a_purchase(self) -> None:
        parsed = self._rows(
            "Date,Description,Amount",
            "01/31/2026,TOTAL WINE 123,-50.00",
            "01/31/2026,BALANCE YOGA STUDIO,-25.00",
        )
        self.assertEqual(len(parsed.rows), 2)

    def test_a_real_row_with_a_bad_date_is_still_an_error(self) -> None:
        parsed = self._rows("Date,Description,Amount", "13/45/2026,HOTEL,-80.00", ",HOTEL,-80.00")
        self.assertEqual(len(parsed.errors), 2)

    def test_statement_file_with_a_summary_line_can_be_compared(self) -> None:
        biz = self.business()
        acct = self.account(biz, opening=0)
        ledger.add_manual(self.conn, acct, date(2025, 1, 5), -100, "ROW")
        path = self.write("s.csv", "Date,Description,Amount\n01/05/2025,ROW,-1.00\n,Statement total,-1.00\n")
        report = reconcile.run(self.conn, acct, date(2025, 1, 1), date(2025, 1, 31), 0, -100, path)
        self.assertTrue(report.balanced)

    def test_statement_file_with_a_bad_row_can_be_compared_on_request(self) -> None:
        biz = self.business()
        acct = self.account(biz, opening=0)
        path = self.write("s.csv", "Date,Description,Amount\nnot a date,ROW,-1.00\n")
        period = (date(2025, 1, 1), date(2025, 1, 31))
        with self.assertRaises(BooksError):
            reconcile.run(self.conn, acct, *period, 0, 0, path)
        report = reconcile.run(self.conn, acct, *period, 0, 0, path, skip_bad=True)
        self.assertTrue(any("left out of the comparison" in note for note in report.notes))


class UnquotedAmounts(BooksCase):
    def test_extra_fields_are_a_bad_line_not_a_smaller_amount(self) -> None:
        path = self.write(
            "x.csv",
            "Date,Desc,Amount,Balance\n"
            "01/06/2026,UNQUOTED OUT,-1,064.33,4035.77\n"
            "01/07/2026,FINE,-5.00,4030.77\n"
            "01/08/2026,TRAILING COMMAS,-5.00,4025.77,,\n",
        )
        parsed = parse_export(path, Mapping(description_column="Desc"))
        self.assertEqual([row.description for row in parsed.rows], ["FINE", "TRAILING COMMAS"])
        self.assertEqual(len(parsed.errors), 1)
        self.assertIn("line 2", parsed.errors[0])
        self.assertIn("missing its quotes", parsed.errors[0])


class BankIds(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz, mapping=WITH_IDS)

    def _count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def _file(self, name: str, *rows: str):
        return self.write(name, "Date,Description,Amount,Ref\n" + "\n".join(rows) + "\n")

    def test_row_added_by_hand_is_not_doubled_and_takes_the_id(self) -> None:
        manual = ledger.add_manual(self.conn, self.acct, date(2026, 3, 25), -1400, "FERN PARKING GARAGE")
        path = self._file("a.csv", "03/25/2026,FERN PARKING GARAGE,-14.00,T1", "03/26/2026,OTHER,-1.00,T2")
        result = importer.import_export(self.conn, self.acct, path)
        self.assertEqual((result.rows_new, result.rows_duplicate), (1, 1))
        self.assertEqual(self._count(), 2)
        self.assertEqual(ledger.get_transaction(self.conn, manual)["reference"], "T1")
        # And the id now identifies it, so the file is still safe to repeat.
        self.assertEqual(importer.import_export(self.conn, self.acct, path).rows_new, 0)

    def test_naming_an_id_column_later_does_not_double_rows(self) -> None:
        plain = self.account(self.biz, "Plain", mapping=Mapping())
        path = self._file("a.csv", "01/02/2026,RENT,-5.00,T1", "01/03/2026,FEE,-1.00,T2")
        importer.import_export(self.conn, plain, path)
        self.assertEqual(self._count(), 2)
        result = importer.import_export(self.conn, plain, path, mapping=WITH_IDS)
        self.assertEqual((result.rows_new, result.rows_duplicate), (0, 2))
        self.assertEqual(self._count(), 2)

    def test_dropping_the_id_column_later_does_not_double_rows(self) -> None:
        path = self._file("a.csv", "01/02/2026,RENT,-5.00,T1", "01/03/2026,FEE,-1.00,T2")
        importer.import_export(self.conn, self.acct, path)
        other = self._file("b.csv", "01/03/2026,FEE,-1.00,", "01/04/2026,NEW,-2.00,")
        result = importer.import_export(self.conn, self.acct, other, mapping=Mapping())
        self.assertEqual((result.rows_new, result.rows_duplicate), (1, 1))

    def test_identical_rows_with_different_ids_stay_separate(self) -> None:
        path = self._file("a.csv", "01/02/2026,COFFEE,-4.50,T1", "01/02/2026,COFFEE,-4.50,T2")
        self.assertEqual(importer.import_export(self.conn, self.acct, path).rows_new, 2)
        self.assertEqual(importer.import_export(self.conn, self.acct, path).rows_new, 0)

    def test_a_row_number_used_as_an_id_stops_instead_of_dropping_rows(self) -> None:
        importer.import_export(self.conn, self.acct, self._file("jan.csv", "01/05/2026,RENT,-500.00,1", "01/10/2026,INVOICE 1,800.00,2"))
        feb = self._file("feb.csv", "02/05/2026,RENT FEB,-500.00,1", "02/10/2026,INVOICE 2,900.00,2")
        with self.assertRaises(BooksError) as caught:
            importer.import_export(self.conn, self.acct, feb)
        message = str(caught.exception)
        self.assertIn("Nothing was imported", message)
        self.assertIn("--no-reference-col", message)
        self.assertIn("RENT FEB", message)
        self.assertEqual(self._count(), 2)
        # With the column dropped, the new rows go in and the old ones still match.
        result = importer.import_export(self.conn, self.acct, feb, mapping=Mapping())
        self.assertEqual(result.rows_new, 2)

    def test_an_id_with_a_changed_amount_is_shown_not_ignored(self) -> None:
        importer.import_export(self.conn, self.acct, self._file("a.csv", "01/05/2026,COFFEE,-4.00,T1"))
        changed = self._file("b.csv", "01/05/2026,COFFEE,-5.50,T1", "01/06/2026,NEW,-1.00,T2")
        with self.assertRaises(BooksError):
            importer.import_export(self.conn, self.acct, changed)
        dry = importer.import_export(self.conn, self.acct, changed, dry_run=True)
        self.assertEqual((dry.rows_new, dry.rows_bad), (1, 1))
        result = importer.import_export(self.conn, self.acct, changed, skip_bad=True)
        self.assertEqual((result.rows_new, result.rows_bad), (1, 1))
        self.assertEqual(self._count(), 2)

    def test_deleted_row_stays_deleted_when_it_has_an_id(self) -> None:
        path = self._file("a.csv", "01/05/2026,COFFEE,-4.00,T1")
        importer.import_export(self.conn, self.acct, path)
        row = self.conn.execute("SELECT id FROM transactions").fetchone()["id"]
        ledger.delete_transaction(self.conn, row)
        self.assertEqual(importer.import_export(self.conn, self.acct, path).rows_deleted_before, 1)

    def test_deleting_one_of_two_identical_rows_sticks(self) -> None:
        plain = self.account(self.biz, "Plain", mapping=Mapping())
        text = "Date,Description,Amount\n01/02/2026,COFFEE,-4.50\n01/02/2026,COFFEE,-4.50\n"
        path = self.write("a.csv", text)
        importer.import_export(self.conn, plain, path)
        first = self.conn.execute("SELECT MIN(id) FROM transactions").fetchone()[0]
        ledger.delete_transaction(self.conn, first)
        result = importer.import_export(self.conn, plain, path)
        self.assertEqual((result.rows_new, result.rows_duplicate, result.rows_deleted_before), (0, 1, 1))


class ChangedMapping(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.card = self.account(self.biz, "A Card", "credit_card", opening=-100000)
        self.path = self.write(
            "card.csv", "Date,Description,Amount\n01/02/2026,A,20.00\n01/03/2026,B,10.00\n01/04/2026,C,20.00\n"
        )

    def test_following_the_sign_warning_without_an_undo_is_stopped(self) -> None:
        first = importer.import_export(self.conn, self.card, self.path)
        self.assertTrue(any("imports undo" in warning for warning in first.warnings))
        with self.assertRaises(BooksError) as caught:
            importer.import_export(self.conn, self.card, self.path, mapping=Mapping(invert_amount=True))
        self.assertIn(f"imports undo {first.import_id}", str(caught.exception))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 3)

    def test_undo_then_new_mapping_gives_the_right_balance(self) -> None:
        first = importer.import_export(self.conn, self.card, self.path)
        importer.undo_import(self.conn, first.import_id)
        importer.import_export(self.conn, self.card, self.path, mapping=Mapping(invert_amount=True))
        self.assertEqual(ledger.balance_through(self.conn, self.card, date(2026, 12, 31)), -105000)



class UndoWithOverlap(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz, "Plain", opening=0)
        self.early = self.write("a.csv", "Date,Description,Amount\n01/02/2026,A,-1.00\n01/02/2026,ONLY EARLY,-4.00\n")
        self.later = self.write("b.csv", "Date,Description,Amount\n01/02/2026,A,-1.00\n01/03/2026,B,-2.00\n")

    def _in_books(self) -> list:
        return [row["description"] for row in self.conn.execute("SELECT description FROM transactions ORDER BY id")]

    def _import(self, path) -> int:
        return importer.import_export(self.conn, self.acct, path).import_id

    def test_a_row_a_later_file_also_held_survives(self) -> None:
        first, second = self._import(self.early), self._import(self.later)
        outcome = importer.undo_import(self.conn, first)
        self.assertEqual((outcome["removed"], outcome["kept"], outcome["kept_in"]), (1, 1, [second]))
        self.assertEqual(self._in_books(), ["A", "B"])
        self.assertEqual(ledger.balance_through(self.conn, self.acct, date(2026, 12, 31)), -300)

    def test_undoing_the_later_file_leaves_the_earlier_files_rows(self) -> None:
        first, second = self._import(self.early), self._import(self.later)
        outcome = importer.undo_import(self.conn, second)
        self.assertEqual((outcome["removed"], outcome["kept"]), (1, 0))
        self.assertEqual(self._in_books(), ["A", "ONLY EARLY"])
        self.assertEqual(importer.undo_import(self.conn, first)["removed"], 2)
        self.assertEqual(self._in_books(), [])

    def test_a_row_goes_when_the_last_file_holding_it_goes(self) -> None:
        third = self.write("c.csv", "Date,Description,Amount\n01/02/2026,A,-1.00\n")
        first, second, last = self._import(self.early), self._import(self.later), self._import(third)
        self.assertEqual(importer.undo_import(self.conn, first)["kept_in"], [second])
        self.assertEqual(importer.undo_import(self.conn, second)["kept_in"], [last])
        self.assertEqual(self._in_books(), ["A"])
        self.assertEqual(importer.undo_import(self.conn, last)["removed"], 1)
        self.assertEqual(self._in_books(), [])

    def test_a_kept_row_keeps_its_hand_booked_category_without_force(self) -> None:
        first = self._import(self.early)
        self._import(self.later)
        row = self.conn.execute("SELECT id FROM transactions WHERE description = 'A'").fetchone()["id"]
        ledger.categorize(self.conn, [row], self.category(self.biz, "6150"))
        importer.undo_import(self.conn, first)
        self.assertEqual(ledger.get_transaction(self.conn, row)["category_source"], "manual")

    def test_a_kept_row_inside_a_finished_reconciliation_does_not_block(self) -> None:
        only_a = self.write("c.csv", "Date,Description,Amount\n01/02/2026,A,-1.00\n")
        first = self._import(only_a)
        self._import(self.later)
        report = reconcile.run(self.conn, self.acct, date(2026, 1, 1), date(2026, 1, 2), 0, -100)
        reconcile.finish(self.conn, report.reconciliation["id"])
        self.assertEqual(importer.undo_import(self.conn, first)["kept"], 1)
        self.assertEqual(self._in_books(), ["A", "B"])

    def test_row_matched_by_id_survives_too(self) -> None:
        mapped = self.account(self.biz, "Ids", mapping=WITH_IDS)
        early = self.write("d.csv", "Date,Description,Amount,Ref\n01/02/2026,A,-1.00,T1\n")
        later = self.write("e.csv", "Date,Description,Amount,Ref\n01/03/2026,A POSTED,-1.00,T1\n")
        first = importer.import_export(self.conn, mapped, early).import_id
        importer.import_export(self.conn, mapped, later)
        self.assertEqual(importer.undo_import(self.conn, first)["kept"], 1)

    def test_a_dry_run_is_not_counted_as_holding_a_row(self) -> None:
        first = self._import(self.early)
        importer.import_export(self.conn, self.acct, self.later, dry_run=True)
        self.assertEqual(importer.undo_import(self.conn, first)["removed"], 2)

    def test_an_undone_file_can_go_back_in(self) -> None:
        first = self._import(self.early)
        self._import(self.later)
        importer.undo_import(self.conn, first)
        again = importer.import_export(self.conn, self.acct, self.early)
        self.assertEqual((again.rows_new, again.rows_duplicate), (1, 1))
        self.assertEqual(sorted(self._in_books()), ["A", "B", "ONLY EARLY"])

    def test_the_command_says_what_stayed(self) -> None:
        self.ok("account", "map", "Plain", "--date-col", "Date")
        self.ok("import", "Plain", str(self.early))
        self.ok("import", "Plain", str(self.later))
        out = self.ok("imports", "undo", "1")
        self.assertIn("Removed 1", out)
        self.assertIn("Kept 1 row(s) that import(s) 2 also held", out)


class CommandLineEdges(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "Bank", "--kind", "checking", "--date-col", "Date")

    def test_negative_amount_with_a_comma(self) -> None:
        self.ok("txn", "add", "Bank", "--date", "2026-01-02", "--amount", "-1,234.50", "--description", "BIG")
        self.ok("txn", "add", "Bank", "--date", "2026-01-03", "--amount", "-$5.00", "--description", "SMALL")
        self.assertIn("net -1,239.50", self.ok("txn", "list"))

    def test_dry_run_says_a_real_run_would_stop(self) -> None:
        path = self.write("a.csv", "Date,Description,Amount\n01/02/2026,OK,-1.00\nnope,BAD,-1.00\n")
        self.assertIn("A real import stops", self.ok("import", "Bank", str(path), "--dry-run"))

    def test_no_reference_col_flag(self) -> None:
        self.ok("account", "map", "Bank", "--reference-col", "Ref")
        self.assertIn('"reference_column": "Ref"', self.ok("account", "show", "Bank"))
        self.ok("account", "map", "Bank", "--no-reference-col")
        self.assertIn('"reference_column": null', self.ok("account", "show", "Bank"))

    def test_adding_a_row_to_a_finished_period_warns(self) -> None:
        self.ok("reconcile", "run", "Bank", "--from", "2026-01-01", "--to", "2026-01-31", "--begin", "0", "--end", "0")
        self.ok("reconcile", "finish", "1")
        out = self.ok("txn", "add", "Bank", "--date", "2026-01-15", "--amount", "-1.00", "--description", "LATE")
        self.assertIn("no longer ties", out)

    def test_rules_test_says_what_it_assumed(self) -> None:
        self.assertIn("--amount", self.run_cli("rules", "test", "ANYTHING")[1])

    def test_mapping_types_are_checked(self) -> None:
        for raw in ({"invert_amount": "false"}, {"date_column": 3}):
            with self.assertRaises(BooksError):
                Mapping.from_dict(raw)

    def test_suggest_handles_semicolons_under_a_preamble(self) -> None:
        path = self.write("s.csv", "Example Bank export\n\nDate;Transaction Detail;Amount\n03/15/2026;RENT;-10.00\n")
        mapping, _ = suggest_mapping(path)
        self.assertEqual((mapping.delimiter, mapping.description_column), (";", "Transaction Detail"))
        self.assertEqual(len(parse_export(path, mapping).rows), 1)
