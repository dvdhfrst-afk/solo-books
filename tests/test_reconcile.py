"""Reconciliation: tying out, showing what does not match, and locking."""
from __future__ import annotations

from datetime import date

from solo_books import importer, ledger, reconcile
from solo_books.errors import BooksError
from tests.helpers import BooksCase

JAN = date(2025, 1, 1), date(2025, 1, 31)
FEB = date(2025, 2, 1), date(2025, 2, 28)


class ReconcileBase(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz, opening=100000)

    def add(self, day: date, cents: int, text: str = "ROW", account=None) -> int:
        return ledger.add_manual(self.conn, account or self.acct, day, cents, text)


class Tying(ReconcileBase):
    def test_ties_to_the_cent(self) -> None:
        self.add(date(2025, 1, 5), -2550)
        self.add(date(2025, 1, 9), 100001)
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 197451)
        self.assertEqual(report.difference, 0)
        self.assertEqual(report.expected_end, 197451)
        self.assertTrue(report.balanced)
        self.assertEqual((report.money_in, report.money_out, report.row_count), (100001, 2550, 2))

    def test_one_cent_off_does_not_tie(self) -> None:
        self.add(date(2025, 1, 5), -2550)
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 97451)
        self.assertEqual(report.difference, 1)
        self.assertFalse(report.balanced)
        with self.assertRaises(BooksError):
            reconcile.finish(self.conn, report.reconciliation["id"])

    def test_period_edges_are_inclusive(self) -> None:
        self.add(date(2024, 12, 31), -1)
        self.add(date(2025, 1, 1), -10)
        self.add(date(2025, 1, 31), -100)
        self.add(date(2025, 2, 1), -1000)
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 99890)
        self.assertEqual((report.activity, report.row_count), (-110, 2))

    def test_card_balances_are_entered_as_owed(self) -> None:
        card = self.account(self.biz, "A Card", "credit_card", opening=-20000)
        self.add(date(2025, 1, 5), -5000, account=card)
        self.add(date(2025, 1, 9), 20000, "PAYMENT", account=card)
        report = reconcile.run(self.conn, card, *JAN, 20000, 5000)
        self.assertTrue(report.balanced)
        self.assertEqual(report.statement_end, -5000)

    def test_rerun_replaces_the_figures_of_an_open_reconciliation(self) -> None:
        first = reconcile.run(self.conn, self.acct, *JAN, 100000, 1)
        second = reconcile.run(self.conn, self.acct, *JAN, 100000, 100000)
        self.assertEqual(first.reconciliation["id"], second.reconciliation["id"])
        self.assertTrue(second.balanced)

    def test_end_before_start_refused(self) -> None:
        with self.assertRaises(BooksError):
            reconcile.run(self.conn, self.acct, JAN[1], JAN[0], 0, 0)


class ShowingWhatDoesNotMatch(ReconcileBase):
    def test_beginning_balance_differs_from_the_books(self) -> None:
        report = reconcile.run(self.conn, self.acct, *JAN, 99000, 99000)
        self.assertEqual(report.difference, 0)
        self.assertEqual(report.begin_gap, -1000)
        self.assertFalse(report.balanced)
        self.assertIn("beginning balance", " ".join(report.hints))

    def test_no_opening_balance_checks_activity_only_and_says_so(self) -> None:
        bare = self.account(self.biz, "Bare")
        report = reconcile.run(self.conn, bare, *JAN, 5, 5)
        self.assertTrue(report.balanced)
        self.assertTrue(any("no opening balance" in note for note in report.notes))

    def test_beginning_must_equal_the_prior_ending(self) -> None:
        first = reconcile.run(self.conn, self.acct, *JAN, 100000, 100000)
        reconcile.finish(self.conn, first.reconciliation["id"])
        bare_report = reconcile.run(self.conn, self.acct, *FEB, 100500, 100500)
        self.assertEqual(bare_report.prior_gap, 500)
        self.assertFalse(bare_report.balanced)

    def test_gap_between_periods_is_noted(self) -> None:
        first = reconcile.run(self.conn, self.acct, *JAN, 100000, 100000)
        reconcile.finish(self.conn, first.reconciliation["id"])
        report = reconcile.run(self.conn, self.acct, date(2025, 3, 1), date(2025, 3, 31), 100000, 100000)
        self.assertTrue(any("have not been reconciled" in note for note in report.notes))

    def test_hint_names_a_row_the_statement_does_not_have(self) -> None:
        row = self.add(date(2025, 1, 5), -1234)
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 100000)
        self.assertEqual(report.difference, 1234)
        self.assertTrue(any(str(row) in hint and "equals the difference" in hint for hint in report.hints))

    def test_hint_names_a_likely_duplicate(self) -> None:
        self.add(date(2025, 1, 5), -500, "COFFEE")
        self.add(date(2025, 1, 5), -500, "COFFEE")
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 99500)
        self.assertEqual(report.difference, 500)
        self.assertTrue(any("may be a duplicate" in hint for hint in report.hints))

    def test_hint_names_a_reversed_sign(self) -> None:
        row = self.add(date(2025, 1, 5), 700)
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 99300)
        self.assertEqual(report.difference, -1400)
        self.assertTrue(any(str(row) in hint and "sign" in hint for hint in report.hints))

    def test_hint_names_a_row_just_outside_the_period(self) -> None:
        row = self.add(date(2025, 2, 2), -900)
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 99100)
        self.assertEqual(report.difference, -900)
        self.assertTrue(any(str(row) in hint and "outside" in hint for hint in report.hints))

    def test_hint_when_nothing_explains_it(self) -> None:
        self.add(date(2025, 1, 5), -1)
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 90000)
        self.assertTrue(any("No single row explains it" in hint for hint in report.hints))

    def _statement(self, *rows: str):
        return self.write("statement.csv", "Date,Description,Amount\n" + "\n".join(rows) + "\n")

    def test_line_by_line_all_buckets(self) -> None:
        self.add(date(2025, 1, 3), -100, "SAME")
        self.add(date(2025, 1, 4), -200, "MOVED DAY")
        self.add(date(2025, 1, 5), -333, "CHANGED AMOUNT")
        self.add(date(2025, 1, 6), -400, "ONLY IN BOOKS")
        path = self._statement(
            "01/03/2025,SAME,-1.00",
            "01/07/2025,MOVED DAY,-2.00",
            "01/05/2025,CHANGED AMOUNT,-3.00",
            "01/08/2025,ONLY ON STATEMENT,-5.00",
            "02/15/2025,NEXT MONTH,-9.00",
        )
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 99000, path)
        self.assertEqual([line.description for line in report.statement_only], ["ONLY ON STATEMENT"])
        self.assertEqual([row["description"] for row in report.book_only], ["ONLY IN BOOKS"])
        self.assertEqual([pair[0].description for pair in report.date_differs], ["MOVED DAY"])
        self.assertEqual([pair[1]["amount_cents"] for pair in report.amount_differs], [-333])
        self.assertEqual(report.statement_rows_outside, 1)
        self.assertFalse(report.balanced)

    def test_identical_rows_pair_one_to_one(self) -> None:
        self.add(date(2025, 1, 3), -100, "COFFEE")
        self.add(date(2025, 1, 3), -100, "COFFEE")
        path = self._statement("01/03/2025,COFFEE,-1.00")
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 99900, path)
        self.assertEqual(len(report.book_only), 1)
        self.assertEqual(report.statement_only, [])

    def test_date_difference_alone_still_ties(self) -> None:
        self.add(date(2025, 1, 4), -200, "MOVED DAY")
        path = self._statement("01/07/2025,MOVED DAY,-2.00")
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 99800, path)
        self.assertEqual(len(report.date_differs), 1)
        self.assertTrue(report.balanced)

    def test_unreadable_statement_is_refused(self) -> None:
        path = self._statement("not a date,X,-1.00")
        with self.assertRaises(BooksError):
            reconcile.run(self.conn, self.acct, *JAN, 100000, 100000, path)


class Locking(ReconcileBase):
    def _finish_january(self, end: int = 99000) -> int:
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, end)
        reconcile.finish(self.conn, report.reconciliation["id"])
        return report.reconciliation["id"]

    def test_finish_locks_rows_against_delete(self) -> None:
        row = self.add(date(2025, 1, 5), -1000)
        recon_id = self._finish_january()
        self.assertEqual(ledger.get_transaction(self.conn, row)["reconciliation_id"], recon_id)
        with self.assertRaises(BooksError):
            ledger.delete_transaction(self.conn, row)

    def test_category_can_still_change_after_finish(self) -> None:
        row = self.add(date(2025, 1, 5), -1000)
        self._finish_january()
        ledger.categorize(self.conn, [row], self.category(self.biz, "6150"))
        self.assertEqual(ledger.get_transaction(self.conn, row)["category_code"], "6150")

    def test_finished_import_cannot_be_undone(self) -> None:
        path = self.write("a.csv", "Date,Description,Amount\n01/05/2025,ROW,-10.00\n")
        result = importer.import_export(self.conn, self.acct, path)
        self._finish_january()
        with self.assertRaises(BooksError):
            importer.undo_import(self.conn, result.import_id)

    def test_overlap_with_finished_period_refused(self) -> None:
        self.add(date(2025, 1, 5), -1000)
        self._finish_january()
        with self.assertRaises(BooksError):
            reconcile.run(self.conn, self.acct, date(2025, 1, 15), date(2025, 2, 15), 0, 0)
        with self.assertRaises(BooksError):
            reconcile.run(self.conn, self.acct, *JAN, 100000, 99000)

    def test_finish_twice_refused(self) -> None:
        self.add(date(2025, 1, 5), -1000)
        recon_id = self._finish_january()
        with self.assertRaises(BooksError):
            reconcile.finish(self.conn, recon_id)

    def test_late_row_is_flagged_on_import_and_in_show(self) -> None:
        self.add(date(2025, 1, 5), -1000)
        recon_id = self._finish_january()
        path = self.write("a.csv", "Date,Description,Amount\n01/20/2025,LATE ROW,-1.00\n")
        result = importer.import_export(self.conn, self.acct, path)
        self.assertTrue(any("inside" in w and str(recon_id) in w for w in result.row_warnings))
        report = reconcile.compute(self.conn, reconcile.get(self.conn, recon_id))
        self.assertEqual(report.difference, 100)
        self.assertEqual([row["description"] for row in report.rows_added_after_finish], ["LATE ROW"])

    def test_reopen_unlocks_and_only_the_latest(self) -> None:
        row = self.add(date(2025, 1, 5), -1000)
        first = self._finish_january()
        second_report = reconcile.run(self.conn, self.acct, *FEB, 99000, 99000)
        reconcile.finish(self.conn, second_report.reconciliation["id"])
        with self.assertRaises(BooksError):
            reconcile.reopen(self.conn, first)
        reconcile.reopen(self.conn, second_report.reconciliation["id"])
        reconcile.reopen(self.conn, first)
        ledger.delete_transaction(self.conn, row)
        with self.assertRaises(BooksError):
            reconcile.reopen(self.conn, first)

    def test_forget_only_for_open(self) -> None:
        report = reconcile.run(self.conn, self.acct, *JAN, 100000, 100000)
        reconcile.forget(self.conn, report.reconciliation["id"])
        self.assertEqual(reconcile.list_for_account(self.conn), [])
        recon_id = self._finish_january(100000)
        with self.assertRaises(BooksError):
            reconcile.forget(self.conn, recon_id)
        with self.assertRaises(BooksError):
            reconcile.get(self.conn, 999)
