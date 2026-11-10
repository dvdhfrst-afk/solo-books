"""The history: every change is recorded with the change itself, and some can be taken back."""
from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from datetime import date
from unittest import mock

from solo_books import books, db, history, ledger, reconcile, review, rules, safety
from solo_books.cli import build_parser
from solo_books.cli.common import command_label
from solo_books.errors import BooksError
from solo_books.web import ReviewServer
from tests.helpers import BooksCase, parse_csv


class Recording(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz, opening=0)

    def kinds(self) -> list:
        return [entry["kind"] for entry in history.list_entries(self.conn)][::-1]

    def test_every_kind_has_words_and_revertible_kinds_are_known(self) -> None:
        for kind, words in history.KINDS.items():
            self.assertRegex(kind, r"^[a-z]+(-[a-z]+)*$")
            self.assertTrue(words)
        self.assertTrue(set(history.REVERTIBLE) <= set(history.KINDS))

    def test_an_entry_can_only_be_written_inside_the_change_it_records(self) -> None:
        with self.assertRaises(AssertionError):
            history.record(self.conn, "note", "a note")
        with db.transaction(self.conn):
            with self.assertRaises(AssertionError):
                history.record(self.conn, "no-such-kind", "anything")

    def test_a_failed_history_write_takes_the_change_back_with_it(self) -> None:
        row = ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE")
        meals = self.category(self.biz, "6150")
        with mock.patch.object(history, "record", side_effect=RuntimeError("disk gone")):
            with self.assertRaises(RuntimeError):
                ledger.set_note(self.conn, row, "lunch with a client")
            with self.assertRaises(RuntimeError):
                ledger.categorize(self.conn, [row], meals)
            with self.assertRaises(RuntimeError):
                rules.add_rule(self.conn, self.biz["id"], "contains", "FERN", meals)
            with self.assertRaises(RuntimeError):
                books.rename_business(self.conn, self.biz, "Business Z")
        after = ledger.get_transaction(self.conn, row)
        self.assertEqual(after["note"], "")
        self.assertIsNone(after["category_id"])
        self.assertEqual(rules.list_rules(self.conn, self.biz["id"]), [])
        self.assertEqual(books.get_business(self.conn, None)["name"], "Business A")

    def test_a_refused_change_writes_no_entry(self) -> None:
        before = len(history.list_entries(self.conn))
        with self.assertRaises(BooksError):
            books.rename_category(self.conn, self.category(self.biz, "6150"), "Travel")
        with self.assertRaises(BooksError):
            ledger.set_note(self.conn, 999, "nowhere")
        self.assertEqual(len(history.list_entries(self.conn)), before)

    def test_the_book_level_changes(self) -> None:
        other = books.add_business(self.conn, "Business B")
        books.rename_business(self.conn, other, "Business C")
        renamed = books.get_business(self.conn, "Business C")
        meals = books.find_category(self.conn, other["id"], "6150")
        books.rename_category(self.conn, meals, "Food")
        books.map_category(self.conn, books.find_category(self.conn, other["id"], "6150"), "27a")
        books.map_category(self.conn, books.find_category(self.conn, other["id"], "6150"), None)
        added = books.add_category(self.conn, other["id"], "6300", "Desks", "expense", "20b")
        books.retire_category(self.conn, added)
        books.set_inventory(self.conn, other["id"], 2025, 100, 200)
        books.set_inventory(self.conn, other["id"], 2025, 150, 250)
        books.delete_business(self.conn, renamed)
        self.assertEqual(
            self.kinds()[-10:],
            ["business-add", "business-rename", "chart-rename", "chart-map", "chart-map", "chart-add", "chart-retire",
             "inventory-set", "inventory-set", "business-delete"],
        )
        inventory = history.entry_detail(history.list_entries(self.conn, kind="inventory")[0])
        self.assertEqual(inventory["before"], {"beginning_cents": 100, "ending_cents": 200})
        self.assertEqual(inventory["after"], {"beginning_cents": 150, "ending_cents": 250})
        rename = history.entry_detail(history.list_entries(self.conn, kind="chart-rename")[0])
        self.assertEqual((rename["before"], rename["after"]), ("Meals", "Food"))
        deleted = history.list_entries(self.conn, kind="business-delete")[0]
        self.assertIsNone(deleted["business_name"])
        self.assertIn("Business C", deleted["summary"])

    def test_account_changes(self) -> None:
        card = books.add_account(self.conn, self.biz, "A Card", "credit_card")
        books.rename_account(self.conn, card, "Card")
        card = books.get_account(self.conn, "Card")
        books.set_account_mapping(self.conn, card, '{"date_column": "Date"}')
        books.set_opening_balance(self.conn, card, -2500, date(2025, 1, 1))
        row = ledger.add_manual(self.conn, card, date(2025, 2, 1), -1200, "SKYWAY AIR")
        books.delete_account(self.conn, books.get_account(self.conn, "Card"), with_rows=True)
        self.assertEqual(
            self.kinds()[-6:], ["account-add", "account-rename", "account-map", "account-opening", "txn-add", "account-delete"]
        )
        gone = history.list_entries(self.conn, kind="account-delete")[0]
        rows = history.entry_rows(self.conn, gone["id"])
        self.assertEqual(rows[0][0], row)
        self.assertEqual(rows[0][1]["amount_cents"], -1200)
        self.assertIsNone(rows[0][2])
        opening = history.entry_detail(history.list_entries(self.conn, kind="account-opening")[0])
        self.assertEqual(opening["after"], {"cents": -2500, "date": "2025-01-01"})

    def test_row_changes_keep_before_and_after(self) -> None:
        meals = self.category(self.biz, "6150")
        row = ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE", meals, "first")
        ledger.edit_manual(self.conn, row, amount_cents=-475)
        ledger.set_note(self.conn, row, "second")
        ledger.uncategorize(self.conn, [row])
        ledger.categorize(self.conn, [row], meals)
        ledger.delete_transaction(self.conn, row)
        self.assertEqual(self.kinds()[-6:], ["txn-add", "txn-edit", "note", "categorize", "categorize", "txn-delete"])
        found = history.list_entries(self.conn, transaction_id=row)
        self.assertEqual(len(found), 6)
        edit = history.entry_rows(self.conn, history.list_entries(self.conn, kind="txn-edit")[0]["id"])[0]
        self.assertEqual((edit[1]["amount_cents"], edit[2]["amount_cents"]), (-450, -475))
        note = history.entry_rows(self.conn, history.list_entries(self.conn, kind="note")[0]["id"])[0]
        self.assertEqual((note[1], note[2]), ({"note": "first"}, {"note": "second"}))
        cleared = history.entry_rows(self.conn, history.list_entries(self.conn, kind="categorize")[1]["id"])[0]
        self.assertEqual(cleared[1]["category"], "6150 Meals")
        self.assertIsNone(cleared[2]["category_id"])

    def test_imports_and_their_undo(self) -> None:
        self.ok("rules", "add", "--contains", "FERN", "--category", "Meals")
        path = self.write("a.csv", "Date,Description,Amount\n2025-01-02,BLUE FERN CAFE,-4.50\n2025-01-03,HARDWARE BARN,-9.00\n")
        self.ok("account", "map", "A Checking", "--date-format", "%Y-%m-%d", "--date-col", "Date",
                "--description-col", "Description", "--amount-col", "Amount")
        self.ok("import", "A Checking", str(path))
        self.ok("import", "A Checking", str(path), "--dry-run")
        imported = history.list_entries(self.conn, kind="import")
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["source"], "import")
        rows = history.entry_rows(self.conn, imported[0]["id"])
        self.assertEqual([after["category"] for _, _, after in rows], ["6150 Meals", None])
        self.assertEqual(history.entry_detail(imported[0])["rows_new"], 2)
        self.ok("imports", "undo", "1")
        undone = history.list_entries(self.conn, kind="import-undo")[0]
        self.assertEqual(len(history.entry_rows(self.conn, undone["id"])), 2)
        self.assertEqual(history.entry_detail(undone)["removed"], 2)

    def test_rules_and_review(self) -> None:
        meals = self.category(self.biz, "6150")
        first = ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE 0102")
        second = ledger.add_manual(self.conn, self.acct, date(2025, 1, 3), -550, "BLUE FERN CAFE 0103")
        held = ledger.add_manual(self.conn, self.acct, date(2025, 1, 4), -650, "BLUE FERN CAFE 0104")
        outcome = review.book_rows(self.conn, self.biz["id"], [first], meals, rule_pattern="BLUE FERN CAFE", held_back=[held])
        self.assertEqual(self.kinds()[-3:], ["rule-add", "rule-hold-back", "rule-apply"])
        applied = history.entry_rows(self.conn, history.list_entries(self.conn, kind="rule-apply")[0]["id"])
        self.assertEqual(sorted(row for row, _, _ in applied), [first, second])
        self.assertEqual(applied[0][2]["rule_id"], outcome["rule_id"])
        rules.apply_rules(self.conn, self.biz["id"])
        self.assertEqual(len(history.list_entries(self.conn, kind="rule-apply")), 1, "a run that changes nothing is not a change")
        rules.delete_rule(self.conn, outcome["rule_id"])
        deleted = history.entry_detail(history.list_entries(self.conn, kind="rule-delete")[0])
        self.assertEqual(deleted["held_back"], [held])
        self.assertEqual(sorted(deleted["booked"]), [first, second])
        path = self.write("rules.csv", "match,pattern,category\ncontains,HARDWARE,6110\ncontains,SKYWAY,6140\n")
        rules.import_rules(self.conn, self.biz["id"], path)
        imported = history.list_entries(self.conn, kind="rule-import")[0]
        self.assertEqual(len(history.entry_detail(imported)["rule_ids"]), 2)
        self.assertEqual(len(history.list_entries(self.conn, kind="rule-add")), 1, "an import is one entry")

    def test_the_review_prompt_and_categorize_name_their_command(self) -> None:
        ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE")
        other = ledger.add_manual(self.conn, self.acct, date(2025, 1, 3), -900, "HARDWARE BARN")
        self.ok("review", stdin="6150\n")
        self.ok("categorize", "--clear", str(other))
        sources = [entry["source"] for entry in history.list_entries(self.conn, kind="categorize")]
        self.assertEqual(sources, ["categorize", "review"])

    def test_reconciliation_changes(self) -> None:
        row = ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE")
        report = reconcile.run(self.conn, self.acct, date(2025, 1, 1), date(2025, 1, 31), 0, -450)
        reconcile.run(self.conn, self.acct, date(2025, 1, 1), date(2025, 1, 31), 0, -450)
        recon = report.reconciliation["id"]
        reconcile.finish(self.conn, recon)
        reconcile.reopen(self.conn, recon)
        reconcile.forget(self.conn, recon)
        self.assertEqual(self.kinds()[-4:], ["reconcile-run", "reconcile-finish", "reconcile-reopen", "reconcile-forget"])
        locked = history.entry_rows(self.conn, history.list_entries(self.conn, kind="reconcile-finish")[0]["id"])
        self.assertEqual(locked, [(row, {"reconciliation_id": None}, {"reconciliation_id": recon})])
        unlocked = history.entry_rows(self.conn, history.list_entries(self.conn, kind="reconcile-reopen")[0]["id"])
        self.assertEqual(unlocked, [(row, {"reconciliation_id": recon}, {"reconciliation_id": None})])
        reconcile.run(self.conn, self.acct, date(2025, 2, 1), date(2025, 2, 28), -450, -450)
        reconcile.run(self.conn, self.acct, date(2025, 2, 1), date(2025, 2, 28), -450, -500)
        changed = history.entry_detail(history.list_entries(self.conn, kind="reconcile-run")[0])
        self.assertEqual(changed["before"], {"begin_cents": -450, "end_cents": -450})
        self.assertEqual(changed["after"], {"begin_cents": -450, "end_cents": -500})

    def test_the_command_is_named_and_cleared_afterward(self) -> None:
        parser = build_parser()
        self.assertEqual(command_label(parser.parse_args(["txn", "note", "1", "x"])), "txn note")
        self.assertEqual(command_label(parser.parse_args(["web"])), "web")
        self.assertEqual(command_label(parser.parse_args(["categorize", "6150", "1"])), "categorize")
        self.ok("chart", "rename", "6150", "Meals out")
        self.assertEqual(history.current_source(), "")
        self.assertEqual(history.list_entries(self.conn, kind="chart-rename")[0]["source"], "chart rename")


class Listing(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)
        self.row = ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE")
        ledger.set_note(self.conn, self.row, "with a client")
        ledger.categorize(self.conn, [self.row], self.category(self.biz, "6150"))

    def test_list_is_newest_first_and_filters(self) -> None:
        out = self.ok("history", "list")
        self.assertLess(out.index("Booked 1 row(s) to 6150 Meals"), out.index("Changed the note"))
        self.assertIn("categorize", self.ok("history", "list", "--kind", "categorize"))
        self.assertNotIn("txn-add", self.ok("history", "list", "--kind", "categorize"))
        self.assertIn("3 change(s)", self.ok("history", "list", "--row", str(self.row)))
        self.assertIn("0 change(s)", self.ok("history", "list", "--row", "999"))
        self.assertIn("txn-add", self.ok("history", "list", "--kind", "txn"))
        self.assertIn("The latest 1 change(s)", self.ok("history", "list", "--limit", "1"))
        self.assertIn("0 change(s)", self.ok("history", "list", "--period", "2001"))
        today = self.conn.execute("SELECT date('now')").fetchone()[0]
        self.assertIn("categorize", self.ok("history", "list", "--period", today[:7]))
        self.assertIn("Business A", self.ok("history", "list", "--business", "Business A"))

    def test_unknown_kind_is_a_plain_error(self) -> None:
        code, _, err = self.run_cli("history", "list", "--kind", "splat")
        self.assertEqual(code, 2)
        self.assertIn("no kind of change called 'splat'", err)

    def test_csv_and_a_file(self) -> None:
        rows = parse_csv(self.ok("history", "list", "--format", "csv", "--all"))
        self.assertEqual([row["kind"] for row in rows][:2], ["categorize", "note"])
        self.assertEqual(rows[0]["command"], "")
        target = self.dir / "history.csv"
        self.assertIn(f"Wrote {target}", self.ok("history", "list", "--format", "csv", "-o", str(target)))
        self.assertTrue(target.read_text(encoding="utf-8-sig").startswith("id,changed_at_utc"))

    def test_csv_neutralizes_formulas(self) -> None:
        ledger.set_note(self.conn, self.row, "x")
        with db.transaction(self.conn):
            history.record(self.conn, "note", "=HYPERLINK(\"http://example.invalid\")", self.biz["id"])
        rows = parse_csv(self.ok("history", "list", "--format", "csv"))
        self.assertTrue(rows[0]["summary"].startswith("'="))

    def test_show_gives_rows_before_and_after(self) -> None:
        entry = history.list_entries(self.conn, kind="categorize")[0]
        out = self.ok("history", "show", str(entry["id"]))
        self.assertIn("Kind:     categorize", out)
        self.assertIn("in the queue", out)
        self.assertIn("6150 Meals (by hand)", out)
        self.assertIn(f"history revert {entry['id']}", out)
        note = history.list_entries(self.conn, kind="note")[0]
        self.assertIn("note 'with a client'", self.ok("history", "show", str(note["id"])))
        self.assertIn("no change 999", self.fails("history", "show", "999"))

    def test_txn_show_points_at_the_history(self) -> None:
        self.assertIn(f"history list --row {self.row}", self.ok("txn", "show", str(self.row)))

    def test_describe_reads_every_shape_it_writes(self) -> None:
        self.assertEqual(history.describe(None), "-")
        self.assertIn("in the queue", history.describe(history.booking(None, None, None, None)))
        self.assertIn("6150 Meals (by a rule 4)", history.describe(history.booking(5, "6150 Meals", "rule", 4)))
        self.assertIn("locked by reconciliation 3", history.describe({"reconciliation_id": 3}))
        self.assertIn("tagged Harbor", history.describe({"tag": "Harbor", "tagged": True}))
        self.assertIn("never booked by rule 2", history.describe({"held_back_from_rule": 2}))
        self.assertIn("no note", history.describe({"note": ""}))


class Reverting(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)
        self.meals = self.category(self.biz, "6150")
        self.repairs = self.category(self.biz, "6110")
        self.row = ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE 0102")
        self.other = ledger.add_manual(self.conn, self.acct, date(2025, 1, 3), -550, "BLUE FERN CAFE 0103")

    def latest(self, kind: str) -> int:
        return history.list_entries(self.conn, kind=kind)[0]["id"]

    def booking(self, row: int) -> tuple:
        found = ledger.get_transaction(self.conn, row)
        return found["category_id"], found["category_source"], found["rule_id"]

    def test_categorize_comes_back_to_the_rule_booking(self) -> None:
        rule_id = rules.add_rule(self.conn, self.biz["id"], "contains", "FERN", self.meals)
        rules.apply_rules(self.conn, self.biz["id"])
        before = self.booking(self.row)
        self.assertEqual(before, (self.meals["id"], "rule", rule_id))
        ledger.categorize(self.conn, [self.row, self.other], self.repairs)
        entry = self.latest("categorize")
        out = self.ok("history", "revert", str(entry))
        self.assertIn("2 row(s) booked as they were before", out)
        self.assertEqual(self.booking(self.row), before)
        self.assertEqual(self.booking(self.other), before)
        reverted = history.get_entry(self.conn, entry)
        taken_back = history.get_entry(self.conn, reverted["reverted_by"])
        self.assertEqual(taken_back["kind"], "revert")
        self.assertEqual(taken_back["source"], "history revert")
        self.assertEqual(history.entry_detail(taken_back), {"reverts": entry, "reverted_kind": "categorize"})
        self.assertEqual(len(history.entry_rows(self.conn, taken_back["id"])), 2)
        self.assertIn("already taken back", self.fails("history", "revert", str(entry)))
        self.assertIn("cannot be taken back", self.fails("history", "revert", str(taken_back["id"])))

    def test_clearing_comes_back_too(self) -> None:
        ledger.categorize(self.conn, [self.row], self.meals)
        ledger.uncategorize(self.conn, [self.row])
        history.revert(self.conn, self.latest("categorize"))
        self.assertEqual(self.booking(self.row), (self.meals["id"], "manual", None))

    def test_a_rule_deleted_since_leaves_the_category(self) -> None:
        rule_id = rules.add_rule(self.conn, self.biz["id"], "contains", "FERN", self.meals)
        rules.apply_rules(self.conn, self.biz["id"])
        ledger.categorize(self.conn, [self.row], self.repairs)
        entry = self.latest("categorize")
        rules.delete_rule(self.conn, rule_id)
        history.revert(self.conn, entry)
        self.assertEqual(self.booking(self.row), (self.meals["id"], "rule", None))
        self.assertIn("by a rule since deleted", self.ok("txn", "show", str(self.row)))

    def test_later_work_is_never_undone(self) -> None:
        ledger.categorize(self.conn, [self.row], self.meals)
        entry = self.latest("categorize")
        ledger.categorize(self.conn, [self.row], self.repairs)
        code, _, err = self.run_cli("history", "revert", str(entry))
        self.assertEqual(code, 2)
        self.assertIn(f"transaction {self.row} was booked again since change {entry}", err)
        self.assertEqual(self.booking(self.row), (self.repairs["id"], "manual", None))
        ledger.delete_transaction(self.conn, self.row)
        self.assertIn("was deleted", self.fails("history", "revert", str(self.latest("categorize"))))

    def test_a_note(self) -> None:
        ledger.set_note(self.conn, self.row, "first")
        ledger.set_note(self.conn, self.row, "second")
        older = history.list_entries(self.conn, kind="note")[1]["id"]
        self.assertIn("got another note", self.fails("history", "revert", str(older)))
        self.ok("history", "revert", str(self.latest("note")))
        self.assertEqual(ledger.get_transaction(self.conn, self.row)["note"], "first")
        self.ok("history", "revert", str(older))
        self.assertEqual(ledger.get_transaction(self.conn, self.row)["note"], "")

    def test_a_category_name(self) -> None:
        books.rename_category(self.conn, self.meals, "Meals out")
        entry = self.latest("chart-rename")
        books.add_category(self.conn, self.biz["id"], "6300", "Meals", "expense")
        self.assertIn("now belongs to category 6300", self.fails("history", "revert", str(entry)))
        self.ok("chart", "rename", "6300", "Desks")
        self.ok("history", "revert", str(entry))
        self.assertEqual(self.category(self.biz, "6150")["name"], "Meals")
        books.rename_category(self.conn, self.category(self.biz, "6150"), "Food")
        first = self.latest("chart-rename")
        books.rename_category(self.conn, self.category(self.biz, "6150"), "Dining")
        self.assertIn("renamed again", self.fails("history", "revert", str(first)))

    def test_a_form_line(self) -> None:
        books.map_category(self.conn, self.meals, "27a")
        moved = self.category(self.biz, "6150")["schedule_c_key"]
        self.assertNotEqual(moved, "meals")
        books.map_category(self.conn, self.category(self.biz, "6150"), None)
        history.revert(self.conn, self.latest("chart-map"))
        self.assertEqual(self.category(self.biz, "6150")["schedule_c_key"], moved)
        older = history.list_entries(self.conn, kind="chart-map")[1]["id"]
        history.revert(self.conn, older)
        self.assertEqual(self.category(self.biz, "6150")["schedule_c_key"], "meals")

    def test_an_added_rule_goes_with_what_it_booked(self) -> None:
        self.ok("rules", "add", "--contains", "FERN", "--category", "Meals")
        ledger.categorize(self.conn, [self.other], self.repairs)
        out = self.ok("history", "revert", str(self.latest("rule-add")))
        self.assertIn("1 row(s) it booked are back in the review queue", out)
        self.assertEqual(rules.list_rules(self.conn, self.biz["id"]), [])
        self.assertEqual(self.booking(self.row), (None, None, None))
        self.assertEqual(self.booking(self.other), (self.repairs["id"], "manual", None), "hand work stays")
        self.assertIn("already taken back", self.fails("history", "revert", str(self.latest("rule-add"))))

    def test_a_deleted_rule_comes_back_whole(self) -> None:
        meals = self.meals
        outcome = review.book_rows(self.conn, self.biz["id"], [self.row], meals, rule_pattern="BLUE FERN CAFE", held_back=[self.other])
        rule_id = outcome["rule_id"]
        self.ok("rules", "delete", str(rule_id))
        self.assertEqual(self.booking(self.row), (meals["id"], "rule", None))
        self.ok("history", "revert", str(self.latest("rule-delete")))
        restored = rules.list_rules(self.conn, self.biz["id"])
        self.assertEqual([(r["id"], r["match_type"], r["pattern"]) for r in restored], [(rule_id, "exact", "BLUE FERN CAFE")])
        self.assertEqual(self.booking(self.row), (meals["id"], "rule", rule_id))
        self.assertEqual(ledger.rules_holding_back(self.conn, self.other), [rule_id])
        rules.apply_rules(self.conn, self.biz["id"])
        self.assertIsNone(self.booking(self.other)[0], "a held-back row stays held back")

    def test_a_deleted_rule_whose_number_was_reused_comes_back_under_a_new_one(self) -> None:
        rule_id = rules.add_rule(self.conn, self.biz["id"], "contains", "FERN", self.meals)
        rules.delete_rule(self.conn, rule_id)
        entry = self.latest("rule-delete")
        reused = rules.add_rule(self.conn, self.biz["id"], "contains", "HARDWARE", self.repairs)
        self.assertEqual(reused, rule_id)
        _, summary = history.revert(self.conn, entry)
        self.assertIn(f"it was rule {rule_id}", summary)
        self.assertEqual(len(rules.list_rules(self.conn, self.biz["id"])), 2)

    def test_a_deleted_rule_cannot_return_to_a_retired_category(self) -> None:
        rule_id = rules.add_rule(self.conn, self.biz["id"], "contains", "FERN", self.meals)
        rules.delete_rule(self.conn, rule_id)
        books.retire_category(self.conn, self.meals)
        self.assertIn("retired", self.fails("history", "revert", str(self.latest("rule-delete"))))

    def test_other_kinds_are_refused_plainly(self) -> None:
        ledger.delete_transaction(self.conn, self.row)
        code, _, err = self.run_cli("history", "revert", str(self.latest("txn-delete")))
        self.assertEqual(code, 2)
        self.assertIn("cannot be taken back from the history", err)
        self.assertNotIn("Traceback", err)
        self.assertIn("no change 999", self.fails("history", "revert", "999"))


class Checked(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "Chk", "--kind", "checking")
        self.ok("txn", "add", "Chk", "--date", "2025-01-02", "--amount", "-4.50", "--description", "BLUE FERN CAFE")

    def test_healthy_history_passes(self) -> None:
        out = self.ok("check")
        self.assertIn("change(s) recorded in the history", out)
        self.assertIn("no problems found", out)

    def test_an_entry_of_an_unknown_kind_is_a_problem(self) -> None:
        self.conn.execute("INSERT INTO history (kind, summary) VALUES ('teleport', 'moved the books to the moon')")
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("kind this program does not know ('teleport')", out)

    def test_unreadable_details_and_a_missing_revert_are_problems(self) -> None:
        self.conn.execute("INSERT INTO history (kind, summary, detail_json) VALUES ('note', 'x', 'not json')")
        self.conn.execute("UPDATE history SET reverted_by = 999 WHERE kind = 'txn-add'")
        out = self.fails("check")
        self.assertIn("details that cannot be read", out)
        self.assertIn("taken back by a change that is not there", out)

    def test_salvage_copies_the_history(self) -> None:
        self.ok("txn", "note", "1", "with a client")
        before = self.conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        rows_before = self.conn.execute("SELECT COUNT(*) FROM history_rows").fetchone()[0]
        target = self.dir / "saved.sqlite3"
        self.assertIn("history: ", self.ok("salvage", str(target)))
        copy = sqlite3.connect(str(target))
        self.addCleanup(copy.close)
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM history").fetchone()[0], before)
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM history_rows").fetchone()[0], rows_before)
        self.assertTrue(safety.check_books(target).healthy)
        note = copy.execute("SELECT detail_json, kind FROM history WHERE kind = 'note'").fetchone()
        self.assertEqual(json.loads(note[0]), {})


class FromTheReviewPage(BooksCase):
    def test_a_booking_on_the_page_is_recorded_as_web(self) -> None:
        biz = self.business()
        row = ledger.add_manual(self.conn, self.account(biz), date(2025, 1, 2), -450, "BLUE FERN CAFE")
        history.set_source(command_label(build_parser().parse_args(["web"])))
        self.addCleanup(history.set_source, "")
        server = ReviewServer(self.db_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        self.addCleanup(client.close)
        body = json.dumps({"ids": [row], "category": "6150"}).encode("utf-8")
        client.request("POST", "/api/categorize", body=body, headers={"X-Solo-Books-Token": server.token})
        self.assertEqual(client.getresponse().status, 200)
        entry = history.list_entries(self.conn, kind="categorize")[0]
        self.assertEqual(entry["source"], "web")
        self.assertEqual(history.entry_rows(self.conn, entry["id"])[0][2]["category"], "6150 Meals")
