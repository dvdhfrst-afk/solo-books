"""Rules made of conditions and actions: each kind, the commands that manage rules, rule packs, and upgrades."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import unittest
from datetime import date
from pathlib import Path

from solo_books import conditions as cond
from solo_books import db, ledger, rules, safety
from solo_books.conditions import Facts
from solo_books.errors import BooksError
from solo_books.text import merchant_key
from tests.helpers import REPO_ROOT, BooksCase
from tests.test_safety import make_v1


def facts(description: str = "COFFEE SHOP #12 DOWNTOWN", amount: int = -450, memo: str = "", account: int = 1,
          day=date(2025, 3, 31)) -> Facts:
    return Facts(account, description, memo, merchant_key(description), amount, day)


class EveryConditionKind(unittest.TestCase):
    def test_contains_looks_at_the_description_the_merchant_and_the_memo(self) -> None:
        rule = cond.text_condition("contains", "coffee shop")
        self.assertTrue(rule.matches(facts()))
        self.assertTrue(cond.text_condition("contains", "MORNING").matches(facts(memo="morning order")))
        self.assertFalse(cond.text_condition("contains", "TEA").matches(facts()))

    def test_starts_with(self) -> None:
        self.assertTrue(cond.text_condition("starts", "COFFEE").matches(facts()))
        self.assertTrue(cond.text_condition("starts_with", "coffee shop").matches(facts()))
        self.assertFalse(cond.text_condition("starts", "SHOP").matches(facts()))

    def test_exact_takes_the_whole_description_or_the_merchant(self) -> None:
        self.assertTrue(cond.text_condition("exact", "coffee shop #12 downtown").matches(facts()))
        self.assertTrue(cond.text_condition("exact", merchant_key("COFFEE SHOP #12 DOWNTOWN")).matches(facts()))
        self.assertFalse(cond.text_condition("exact", "COFFEE").matches(facts()))

    def test_regex_looks_at_the_description_and_the_memo(self) -> None:
        self.assertTrue(cond.text_condition("regex", r"^coffee\s+shop").matches(facts()))
        self.assertTrue(cond.text_condition("regex", r"INV-\d+").matches(facts(memo="inv-2041")))
        self.assertFalse(cond.text_condition("regex", r"^SHOP").matches(facts()))
        for bad in ("(", "(a+)+", "x" * (cond.MAX_PATTERN + 1)):
            with self.assertRaises(BooksError):
                cond.text_condition("regex", bad)

    def test_text_needs_wording_and_a_known_match_type(self) -> None:
        with self.assertRaises(BooksError):
            cond.text_condition("contains", "  ")
        with self.assertRaises(BooksError):
            cond.text_condition("sounds_like", "COFFEE")

    def test_amounts_are_sizes(self) -> None:
        equals = cond.amount_condition(450, 450)
        self.assertEqual(equals.kind, "amount_equals")
        self.assertTrue(equals.matches(facts(amount=-450)))
        self.assertTrue(equals.matches(facts(amount=450)))
        self.assertFalse(equals.matches(facts(amount=-451)))
        at_least = cond.amount_condition(1000, None)
        self.assertEqual(at_least.kind, "amount_at_least")
        self.assertTrue(at_least.matches(facts(amount=-1000)))
        self.assertFalse(at_least.matches(facts(amount=-999)))
        at_most = cond.amount_condition(None, 1000)
        self.assertEqual(at_most.kind, "amount_at_most")
        self.assertTrue(at_most.matches(facts(amount=1000)))
        self.assertFalse(at_most.matches(facts(amount=1001)))
        between = cond.amount_condition(400, 500)
        self.assertEqual(between.kind, "amount_between")
        self.assertTrue(all(between.matches(facts(amount=a)) for a in (-400, 450, -500)))
        self.assertFalse(any(between.matches(facts(amount=a)) for a in (-399, 501)))

    def test_amount_mistakes_are_refused(self) -> None:
        for low, high in ((-1, None), (None, -5), (500, 400), (None, None)):
            with self.assertRaises(BooksError):
                cond.amount_condition(low, high)

    def test_direction(self) -> None:
        money_in, money_out = cond.direction_condition("in"), cond.direction_condition("OUT")
        self.assertTrue(money_in.matches(facts(amount=100)))
        self.assertFalse(money_in.matches(facts(amount=-100)))
        self.assertTrue(money_out.matches(facts(amount=-100)))
        self.assertFalse(money_in.matches(facts(amount=0)) or money_out.matches(facts(amount=0)))
        with self.assertRaises(BooksError):
            cond.direction_condition("sideways")

    def test_account(self) -> None:
        self.assertTrue(cond.account_condition(3).matches(facts(account=3)))
        self.assertFalse(cond.account_condition(3).matches(facts(account=4)))
        self.assertFalse(cond.Condition("account").matches(facts()))

    def test_date_between(self) -> None:
        both = cond.first_and_last_day("2025-03-01", "2025-03-31")
        self.assertTrue(both.matches(facts(day=date(2025, 3, 1))))
        self.assertTrue(both.matches(facts(day=date(2025, 3, 31))))
        self.assertFalse(both.matches(facts(day=date(2025, 4, 1))))
        self.assertFalse(both.matches(facts(day=None)))
        self.assertTrue(cond.first_and_last_day("2025-06-01", None).matches(facts(day=date(2026, 1, 1))))
        self.assertFalse(cond.first_and_last_day(None, "2025-06-01").matches(facts(day=date(2025, 6, 2))))
        for start, end in (("2025-04-01", "2025-03-01"), (None, None), ("2025-02-30", None)):
            with self.assertRaises(BooksError):
                cond.first_and_last_day(start, end)

    def test_day_of_month_and_its_wrap(self) -> None:
        early = cond.day_range(1, 10)
        self.assertTrue(early.matches(facts(day=date(2025, 2, 10))))
        self.assertFalse(early.matches(facts(day=date(2025, 2, 11))))
        wraps = cond.day_range(25, 5)
        for day, wanted in ((25, True), (31, True), (1, True), (5, True), (6, False), (24, False)):
            self.assertEqual(wraps.matches(facts(day=date(2025, 1, day))), wanted, day)
        self.assertFalse(early.matches(facts(day=None)))
        self.assertEqual((cond.day_range(None, 5).low, cond.day_range(20, None).high), (1, 31))
        for first, last in ((0, 5), (1, 32), (None, None)):
            with self.assertRaises(BooksError):
                cond.day_range(first, last)

    def test_every_kind_is_described_and_documented(self) -> None:
        doc = (REPO_ROOT / "docs" / "RULES.md").read_text(encoding="utf-8")
        examples = {
            "contains": "contains 'CAFE'", "starts_with": "starts with 'CAFE'", "exact": "is exactly 'CAFE'",
            "regex": "matches the regular expression '^CAFE'",
        }
        for kind, said in examples.items():
            text = "^CAFE" if kind == "regex" else "CAFE"
            self.assertEqual(cond.text_condition(kind, text).describe({}), said)
        self.assertEqual(cond.amount_condition(85000, 85000).describe({}), "size is 850.00")
        self.assertEqual(cond.amount_condition(100, 200).describe({}), "size is from 1.00 to 2.00")
        self.assertEqual(cond.account_condition(2).describe({2: "A Card"}), "account is A Card")
        self.assertEqual(cond.first_and_last_day("2025-01-01", None).describe({}), "date on or after 2025-01-01")
        self.assertEqual(cond.day_range(25, 5).describe({}), "day of month from 25 to 5")
        for kind in cond.CONDITION_KINDS:
            self.assertIn(f"`{kind}`", doc)
        for kind in cond.ACTION_KINDS:
            self.assertIn(f"`{kind}`", doc)

    def test_a_rule_holds_one_condition_per_group_and_at_least_one(self) -> None:
        with self.assertRaises(BooksError):
            cond.check_conditions([])
        with self.assertRaises(BooksError):
            cond.check_conditions([cond.amount_condition(1, None), cond.amount_condition(None, 9)])
        ordered = cond.check_conditions([cond.day_range(1, 2), cond.text_condition("contains", "X")])
        self.assertEqual([c.group for c in ordered], ["description", "day"])

    def test_a_rule_with_no_conditions_matches_nothing(self) -> None:
        self.assertFalse(rules.Rule(1, 1, []).test(facts()))

    def test_notes_are_cleaned_and_limited(self) -> None:
        self.assertEqual(cond.clean_note(None), "")
        with self.assertRaises(BooksError):
            cond.clean_note("x" * (cond.MAX_NOTE + 1))


class EngineCase(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("business", "add", "Business B")
        for name, business in (("A Checking", "Business A"), ("A Card", "Business A"), ("B Checking", "Business B")):
            self.ok("account", "add", name, "--business", business, "--kind", "checking", "--date-format", "%Y-%m-%d")
        self.rows_seen = 0

    def bring_in(self, account: str, *rows: str) -> None:
        self.rows_seen += 1
        path = self.write(f"rows-{self.rows_seen}.csv", "Date,Description,Amount\n" + "\n".join(rows) + "\n")
        self.ok("import", account, str(path))

    def booked(self, description: str) -> tuple:
        row = self.conn.execute(
            "SELECT c.code, t.rule_id, t.note, t.note_from_rule FROM transactions t"
            " LEFT JOIN categories c ON c.id = t.category_id WHERE t.description = ?",
            (description,),
        ).fetchone()
        return tuple(row)

    def txn_id(self, description: str) -> int:
        return self.conn.execute("SELECT id FROM transactions WHERE description = ?", (description,)).fetchone()[0]

    def rule_ids(self, business: str = "Business A") -> list:
        bid = self.conn.execute("SELECT id FROM businesses WHERE name = ?", (business,)).fetchone()[0]
        return [rule.id for rule in rules.load_rules(self.conn, bid, include_disabled=True)]

    def last_rule(self) -> int:
        return self.conn.execute("SELECT MAX(id) FROM rules").fetchone()[0]


class AddingRules(EngineCase):
    def test_the_readme_rent_rule(self) -> None:
        out = self.ok(
            "rules", "add", "--business", "Business A", "--exact", "REDWOOD PLAZA RENT", "--amount-min", "850.00",
            "--amount-max", "850.00", "--day-from", "1", "--day-to", "10", "--note", "Suite 4 lease", "--category", "Office rent",
        )
        self.assertIn("is exactly 'REDWOOD PLAZA RENT' and size is 850.00 and day of month from 1 to 10", out)
        self.bring_in(
            "A Checking", "2025-02-03,REDWOOD PLAZA RENT,-850.00", "2025-02-20,REDWOOD PLAZA RENT LATE,-850.00",
        )
        self.bring_in("A Checking", "2025-03-14,REDWOOD PLAZA RENT,-850.00")
        self.assertEqual(self.booked("REDWOOD PLAZA RENT")[0], "6105")
        rows = self.conn.execute(
            "SELECT posted_on, category_id, note, note_from_rule FROM transactions WHERE description = 'REDWOOD PLAZA RENT' ORDER BY posted_on"
        ).fetchall()
        self.assertEqual([(r["note"], r["note_from_rule"]) for r in rows], [("Suite 4 lease", 1), ("", 0)])
        self.assertIsNone(rows[1]["category_id"])
        self.assertIsNone(self.booked("REDWOOD PLAZA RENT LATE")[0])

    def test_each_flag_makes_its_condition(self) -> None:
        for flags, kind in (
            (("--contains", "A"), "contains"), (("--starts-with", "A"), "starts_with"), (("--starts", "A"), "starts_with"),
            (("--exact", "A"), "exact"), (("--regex", "^A"), "regex"), (("--amount-min", "1.00"), "amount_at_least"),
            (("--min", "1.00", "--max", "2.00"), "amount_between"), (("--amount-max", "2.00"), "amount_at_most"),
            (("--min", "2.00", "--max", "2.00"), "amount_equals"), (("--direction", "in"), "direction"),
            (("--account", "A Card"), "account"), (("--from", "2025-01-01"), "date_between"),
            (("--to", "2025-01-01"), "date_between"), (("--day-from", "3"), "day_of_month_between"),
            (("--day-to", "3"), "day_of_month_between"),
        ):
            self.ok("rules", "add", "--business", "Business A", *flags, "--category", "Meals")
            kinds = [row[0] for row in self.conn.execute("SELECT kind FROM rule_conditions WHERE rule_id = ?", (self.last_rule(),))]
            self.assertEqual(kinds, [kind], flags)

    def test_mistakes_are_refused_in_one_sentence(self) -> None:
        self.assertIn("exactly one", self.fails("rules", "add", "--business", "Business A", "--category", "Meals"))
        self.assertIn("at most one", self.fails("rules", "add", "--business", "Business A", "--contains", "A", "--exact", "B", "--category", "Meals"))
        self.assertIn("another business", self.fails("rules", "add", "--business", "Business B", "--account", "A Card", "--category", "Meals"))
        self.assertIn("minus sign", self.fails("rules", "add", "--business", "Business A", "--min", "-5.00", "--category", "Meals"))
        self.assertIn("comes after", self.fails("rules", "add", "--business", "Business A", "--from", "2025-02-01", "--to", "2025-01-01", "--category", "Meals"))
        self.assertNotEqual(self.run_cli("rules", "add", "--business", "Business A", "--day-from", "40", "--category", "Meals")[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0], 0)

    def test_old_callers_still_add_rules(self) -> None:
        business = self.conn.execute("SELECT * FROM businesses WHERE name = 'Business A'").fetchone()
        account = self.conn.execute("SELECT * FROM accounts WHERE name = 'A Card'").fetchone()
        category = self.conn.execute("SELECT * FROM categories WHERE business_id = ? AND code = '6150'", (business["id"],)).fetchone()
        rule_id = rules.add_rule(
            self.conn, business["id"], "contains", "CAFE", category, direction="out", account_id=account["id"], min_cents=100,
        )
        rule = rules.get_rule(self.conn, rule_id)
        self.assertEqual((rule.match_type, rule.pattern, rule.direction, rule.account_id, rule.min_cents), ("contains", "CAFE", "out", account["id"], 100))
        self.assertTrue(rule.matches(account["id"], "CAFE 9", "", merchant_key("CAFE 9"), -500))
        self.assertFalse(rule.matches(account["id"], "CAFE 9", "", merchant_key("CAFE 9"), 500))


class ManagingRules(EngineCase):
    def setUp(self) -> None:
        super().setUp()
        for word, category in (("ALPHA", "Meals"), ("BETA", "Travel"), ("GAMMA", "Supplies")):
            self.ok("rules", "add", "--business", "Business A", "--contains", word, "--category", category)
        self.alpha, self.beta, self.gamma = self.rule_ids()

    def test_list_shows_the_order_and_what_each_rule_does(self) -> None:
        listing = self.ok("rules", "list", "--business", "Business A")
        self.assertRegex(listing, r"#\s+Id\s+Prio\s+On\s+Conditions\s+Books to\s+Rows")
        self.assertRegex(listing, rf"1\s+{self.alpha}\s+100\s+yes\s+contains 'ALPHA'\s+6150 Meals")

    def test_move_to_a_place_and_before_another(self) -> None:
        self.assertIn("now number 1", self.ok("rules", "move", str(self.gamma), "--to", "1"))
        self.assertEqual(self.rule_ids(), [self.gamma, self.alpha, self.beta])
        priorities = [r[0] for r in self.conn.execute("SELECT priority FROM rules WHERE business_id = 1 ORDER BY priority")]
        self.assertEqual(priorities, [1, 2, 3])
        self.ok("rules", "move", str(self.beta), "--before", str(self.alpha))
        self.assertEqual(self.rule_ids(), [self.gamma, self.beta, self.alpha])
        self.ok("rules", "move", str(self.gamma), "--to", "3")
        self.assertEqual(self.rule_ids(), [self.beta, self.alpha, self.gamma])

    def test_move_mistakes(self) -> None:
        self.assertIn("from 1 to 3", self.fails("rules", "move", str(self.alpha), "--to", "4"))
        self.assertIn("before itself", self.fails("rules", "move", str(self.alpha), "--before", str(self.alpha)))
        self.ok("rules", "add", "--business", "Business B", "--contains", "DELTA", "--category", "Meals")
        self.assertIn("another business", self.fails("rules", "move", str(self.alpha), "--before", str(self.last_rule())))
        self.assertNotEqual(self.run_cli("rules", "move", str(self.alpha))[0], 0)
        self.assertIn("no rule", self.fails("rules", "move", "999", "--to", "1"))

    def test_the_first_matching_rule_wins_and_moving_changes_it(self) -> None:
        self.bring_in("A Checking", "2025-01-05,ALPHA BETA,-10.00")
        self.assertEqual(self.booked("ALPHA BETA")[:2], ("6150", self.alpha))
        self.ok("rules", "move", str(self.beta), "--to", "1")
        self.assertEqual(self.booked("ALPHA BETA")[0], "6150")
        self.assertIn("changed 1", self.ok("rules", "apply", "--recheck"))
        self.assertEqual(self.booked("ALPHA BETA")[:2], ("6140", self.beta))

    def test_disable_and_enable(self) -> None:
        self.assertIn("disabled", self.ok("rules", "disable", str(self.alpha)))
        self.assertRegex(self.ok("rules", "list", "--business", "Business A"), rf"{self.alpha}\s+100\s+off\s")
        self.bring_in("A Checking", "2025-01-05,ALPHA ONE,-10.00")
        self.assertIsNone(self.booked("ALPHA ONE")[0])
        self.assertEqual(self.run_cli("rules", "test", "ALPHA ONE", "--business", "Business A")[0], 1)
        self.assertIn("booked 1 row", self.ok("rules", "enable", str(self.alpha)))
        self.assertEqual(self.booked("ALPHA ONE")[0], "6150")
        self.ok("rules", "disable", str(self.alpha))
        self.assertIn("back to review 1", self.ok("rules", "apply", "--recheck"))
        self.assertIn("no rule", self.fails("rules", "enable", "999"))

    def test_edit_replaces_drops_and_rebooks(self) -> None:
        out = self.ok("rules", "edit", str(self.alpha), "--direction", "out", "--category", "Travel", "--note", "trip")
        self.assertIn("contains 'ALPHA' and money out", out)
        rule = rules.get_rule(self.conn, self.alpha)
        self.assertEqual((rule.direction, rule.note), ("out", "trip"))
        self.ok("rules", "edit", str(self.alpha), "--contains", "OMEGA")
        self.assertEqual(rules.get_rule(self.conn, self.alpha).pattern, "OMEGA")
        self.ok("rules", "edit", str(self.alpha), "--drop", "direction", "--no-note")
        rule = rules.get_rule(self.conn, self.alpha)
        self.assertEqual(([c.kind for c in rule.conditions], rule.note), (["contains"], ""))
        self.assertIn("say what to change", self.fails("rules", "edit", str(self.alpha)))
        self.assertIn("at least one condition", self.fails("rules", "edit", str(self.alpha), "--drop", "description"))
        self.assertNotEqual(self.run_cli("rules", "edit", str(self.alpha), "--note", "x", "--no-note")[0], 0)
        self.assertIn("no category", self.fails("rules", "edit", str(self.alpha), "--category", "Nonsense"))

    def test_explain_a_row(self) -> None:
        self.bring_in("A Checking", "2025-01-05,ALPHA BETA,-10.00", "2025-01-06,NOTHING HERE,-3.00")
        self.ok("rules", "disable", str(self.gamma))
        code, out, _ = self.run_cli("rules", "explain", str(self.txn_id("ALPHA BETA")))
        self.assertEqual(code, 0)
        self.assertIn(f"1. rule {self.alpha} -> 6150 Meals: MATCHES, and books the row", out)
        self.assertIn(f"2. rule {self.beta} -> 6140 Travel: matches too, but rule {self.alpha} comes first", out)
        self.assertIn("disabled, so it is skipped", out)
        self.assertIn("contains 'ALPHA': yes", out)
        code, out, _ = self.run_cli("rules", "explain", str(self.txn_id("NOTHING HERE")))
        self.assertEqual(code, 1)
        self.assertIn("No enabled rule books this row.", out)
        self.ok("categorize", "Supplies", str(self.txn_id("ALPHA BETA")))
        self.assertIn("booked by hand", self.ok("rules", "explain", str(self.txn_id("ALPHA BETA"))))
        self.assertIn("no transaction 999", self.fails("rules", "explain", "999"))

    def test_explain_says_when_a_recheck_would_change_a_row(self) -> None:
        self.bring_in("A Checking", "2025-01-05,ALPHA BETA,-10.00")
        self.ok("rules", "move", str(self.beta), "--to", "1")
        self.assertIn(f"rule {self.beta} matches first now", self.ok("rules", "explain", str(self.txn_id("ALPHA BETA"))))

    def test_test_with_a_date_a_memo_and_why(self) -> None:
        self.ok("rules", "add", "--business", "Business A", "--contains", "LEASE", "--day-from", "1", "--day-to", "5", "--category", "Office rent")
        code, out, _ = self.run_cli("rules", "test", "LEASE CO", "--business", "Business A")
        self.assertEqual(code, 1)
        self.assertIn("Give one with --date", out)
        out = self.ok("rules", "test", "LEASE CO", "--business", "Business A", "--date", "2025-04-02", "--why")
        self.assertIn("books it to 6105 Office rent", out)
        self.assertIn("day of month from 1 to 5: yes (the day of the month is 2)", out)
        self.assertIn(f"rule {self.alpha} -> 6150 Meals: does not match", out)
        self.assertIn("contains 'ALPHA'", self.ok("rules", "test", "WIRE", "--memo", "alpha invoice", "--business", "Business A"))

    def test_delete_keeps_the_rows_category(self) -> None:
        self.bring_in("A Checking", "2025-01-05,ALPHA ONE,-10.00")
        self.ok("rules", "delete", str(self.alpha))
        self.assertEqual(self.booked("ALPHA ONE")[:2], ("6150", None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rule_conditions WHERE rule_id = ?", (self.alpha,)).fetchone()[0], 0)


class NoteAction(EngineCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("rules", "add", "--business", "Business A", "--contains", "STORAGE", "--note", "unit 9", "--category", "Office rent")

    def test_a_rule_note_goes_on_rows_without_one_and_is_marked(self) -> None:
        self.bring_in("A Checking", "2025-01-05,STORAGE PLUS,-60.00")
        self.assertEqual(self.booked("STORAGE PLUS")[2:], ("unit 9", 1))
        self.assertIn("unit 9  (from a rule)", self.ok("txn", "show", str(self.txn_id("STORAGE PLUS"))))

    def test_a_note_someone_wrote_is_never_replaced(self) -> None:
        self.ok("rules", "disable", str(self.last_rule()))
        self.bring_in("A Checking", "2025-01-05,STORAGE PLUS,-60.00")
        txn = self.txn_id("STORAGE PLUS")
        self.ok("txn", "note", str(txn), "my own words")
        self.ok("rules", "enable", str(self.last_rule()))
        self.assertEqual(self.booked("STORAGE PLUS"), ("6105", self.last_rule(), "my own words", 0))
        self.ok("rules", "edit", str(self.last_rule()), "--no-note")
        self.ok("rules", "apply", "--recheck")
        self.assertEqual(self.booked("STORAGE PLUS")[2:], ("my own words", 0))

    def test_editing_a_rule_written_note_takes_it_over(self) -> None:
        self.bring_in("A Checking", "2025-01-05,STORAGE PLUS,-60.00")
        ledger.set_note(self.conn, self.txn_id("STORAGE PLUS"), "unit 9, gate code on file")
        self.assertEqual(self.booked("STORAGE PLUS")[3], 0)

    def test_a_recheck_updates_or_clears_a_rule_written_note(self) -> None:
        self.bring_in("A Checking", "2025-01-05,STORAGE PLUS,-60.00")
        self.ok("rules", "edit", str(self.last_rule()), "--note", "unit 10")
        self.assertEqual(self.booked("STORAGE PLUS")[2], "unit 9")
        self.ok("rules", "apply", "--recheck")
        self.assertEqual(self.booked("STORAGE PLUS")[2:], ("unit 10", 1))
        self.ok("rules", "edit", str(self.last_rule()), "--no-note")
        self.ok("rules", "apply", "--recheck")
        self.assertEqual(self.booked("STORAGE PLUS")[2:], ("", 0))
        self.ok("rules", "edit", str(self.last_rule()), "--note", "unit 11")
        self.ok("rules", "disable", str(self.last_rule()))
        self.ok("rules", "apply", "--recheck")
        self.assertEqual(self.booked("STORAGE PLUS"), (None, None, "", 0))


class AccountsAndRules(EngineCase):
    def test_a_rule_limited_to_an_account_goes_when_the_account_goes(self) -> None:
        self.ok("rules", "add", "--business", "Business A", "--contains", "X", "--account", "A Card", "--category", "Meals")
        self.ok("rules", "add", "--business", "Business A", "--contains", "Y", "--category", "Meals")
        self.conn.execute("DELETE FROM accounts WHERE name = 'A Card'")
        left = [row[0] for row in self.conn.execute("SELECT pattern FROM rules")]
        self.assertEqual(left, ["Y"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rule_conditions").fetchone()[0], 1)


class RuleFiles(EngineCase):
    def setUp(self) -> None:
        super().setUp()
        self.ok("rules", "add", "--business", "Business A", "--contains", "CAFE", "--direction", "out", "--category", "Meals")
        self.ok("rules", "add", "--business", "Business A", "--exact", "REDWOOD PLAZA RENT", "--min", "850.00", "--max", "850.00",
                "--day-from", "25", "--day-to", "5", "--note", "Suite 4", "--category", "Office rent")
        self.ok("rules", "add", "--business", "Business A", "--regex", r"^AIR\s", "--account", "A Card", "--from", "2025-01-01",
                "--to", "2025-12-31", "--priority", "50", "--category", "Travel")
        self.ok("rules", "add", "--business", "Business A", "--starts", "BIG", "--amount-min", "100.00", "--category", "Supplies")
        self.ok("rules", "disable", str(self.last_rule()))
        self.pack = self.dir / "rules-a.json"
        self.ok("rules", "export", "--business", "Business A", "-o", str(self.pack))

    def pack_data(self) -> dict:
        return json.loads(self.pack.read_text(encoding="utf-8"))

    def write_pack(self, data) -> Path:
        return self.write("pack.json", data if isinstance(data, str) else json.dumps(data))

    def test_the_pack_carries_everything(self) -> None:
        data = self.pack_data()
        self.assertEqual(data["pack_version"], 1)
        self.assertEqual(len(data["rules"]), 4)
        rent = [r for r in data["rules"] if r["actions"][0]["category"] == "6105"][0]
        self.assertEqual(rent["conditions"], [
            {"kind": "exact", "text": "REDWOOD PLAZA RENT"}, {"kind": "amount_equals", "amount": "850.00"},
            {"kind": "day_of_month_between", "from": 25, "to": 5},
        ])
        self.assertEqual(rent["actions"][1], {"kind": "set_note", "note": "Suite 4"})
        air = data["rules"][0]
        self.assertEqual(air["priority"], 50)
        self.assertIn({"kind": "account", "account": "A Card"}, air["conditions"])
        self.assertIn({"kind": "date_between", "from": "2025-01-01", "to": "2025-12-31"}, air["conditions"])
        self.assertFalse(data["rules"][-1]["enabled"])

    def test_importing_the_same_pack_again_adds_nothing(self) -> None:
        out = self.ok("rules", "import", str(self.pack), "--business", "Business A")
        self.assertIn("Added 0 rule(s) to Business A; skipped 4", out)
        self.assertEqual(len(self.rule_ids()), 4)

    def test_a_round_trip_into_other_books_keeps_every_rule(self) -> None:
        other = self.dir / "other.sqlite3"
        def there(*args: str) -> str:
            code, out, err = self.run_cli_on(other, *args)
            self.assertEqual(code, 0, err)
            return out
        there("init")
        there("business", "add", "Business A")
        there("account", "add", "A Card", "--kind", "credit_card", "--date-format", "%Y-%m-%d")
        self.assertIn("Added 4 rule(s)", there("rules", "import", str(self.pack)))
        again = self.dir / "again.json"
        there("rules", "export", "-o", str(again))
        self.assertEqual(json.loads(again.read_text(encoding="utf-8")), self.pack_data())

    def run_cli_on(self, path: Path, *args: str):
        saved = self.db_path
        self.db_path = path
        try:
            return self.run_cli(*args)
        finally:
            self.db_path = saved

    def test_one_bad_rule_adds_none(self) -> None:
        data = self.pack_data()
        data["rules"].insert(0, {"conditions": [{"kind": "contains", "text": "NEW"}], "actions": [{"kind": "book_category", "category": "6150"}]})
        data["rules"].append({"conditions": [{"kind": "contains", "text": "BAD"}], "actions": [{"kind": "book_category", "category": "9999"}]})
        err = self.fails("rules", "import", str(self.write_pack(data)), "--business", "Business A")
        self.assertIn("pack.json rule 6: no category", err)
        self.assertIn("No rules were added", err)
        self.assertEqual(len(self.rule_ids()), 4)

    def test_the_pack_version_is_checked(self) -> None:
        for says, data in (
            ("no pack_version", {"rules": []}), ("reads up to version 1", {"pack_version": 2, "rules": []}),
            ("not a version number", {"pack_version": "1", "rules": []}), ("not a version number", {"pack_version": True, "rules": []}),
        ):
            self.assertIn(says, self.fails("rules", "import", str(self.write_pack(data)), "--business", "Business B"))

    def test_unknown_and_wrong_keys_are_refused(self) -> None:
        good = {"conditions": [{"kind": "contains", "text": "NEW"}], "actions": [{"kind": "book_category", "category": "6150"}]}
        cases = {
            "unknown key(s): extra": {"pack_version": 1, "rules": [], "extra": 1},
            "unknown key(s): colour": {"pack_version": 1, "rules": [{**good, "colour": "red"}]},
            "has unknown key(s): pattern": {"pack_version": 1, "rules": [{**good, "conditions": [{"kind": "contains", "text": "A", "pattern": "B"}]}]},
            "unknown condition kind 'sounds_like'": {"pack_version": 1, "rules": [{**good, "conditions": [{"kind": "sounds_like"}]}]},
            "unknown action 'send_email'": {"pack_version": 1, "rules": [{**good, "actions": [{"kind": "send_email"}]}]},
            "needs a list of rules": {"pack_version": 1, "rules": {}},
            "enabled must be true or false": {"pack_version": 1, "rules": [{**good, "enabled": "yes"}]},
            "priority must be a whole number": {"pack_version": 1, "rules": [{**good, "priority": 1.5}]},
            "only one amount": {"pack_version": 1, "rules": [{**good, "conditions": [
                {"kind": "amount_at_least", "amount": "1.00"}, {"kind": "amount_at_most", "amount": "9.00"}]}]},
        }
        for says, data in cases.items():
            self.assertIn(says, self.fails("rules", "import", str(self.write_pack(data)), "--business", "Business B"))
        self.assertIn("not valid JSON", self.fails("rules", "import", str(self.write_pack("{")), "--business", "Business B"))
        self.assertEqual(self.rule_ids("Business B"), [])

    def test_amounts_in_a_pack_are_text(self) -> None:
        data = {"pack_version": 1, "rules": [{"conditions": [{"kind": "amount_equals", "amount": 12.5}],
                                              "actions": [{"kind": "book_category", "category": "6150"}]}]}
        self.assertNotEqual(self.run_cli("rules", "import", str(self.write_pack(data)), "--business", "Business B")[0], 0)
        data["rules"][0]["conditions"][0]["amount"] = "12.50"
        self.ok("rules", "import", str(self.write_pack(data)), "--business", "Business B")
        bid = self.conn.execute("SELECT id FROM businesses WHERE name = 'Business B'").fetchone()[0]
        self.assertEqual(rules.load_rules(self.conn, bid)[0].conditions[0].low, 1250)

    def test_a_pack_account_must_belong_to_the_business(self) -> None:
        self.assertIn("another business", self.fails("rules", "import", str(self.pack), "--business", "Business B"))

    def test_the_csv_export_refuses_what_its_columns_cannot_carry(self) -> None:
        err = self.fails("rules", "export", "--business", "Business A", "--format", "csv")
        self.assertIn("--format json", err)
        self.assertNotEqual(self.run_cli("rules", "export", "--business", "Business A")[0], 0)
        self.ok("rules", "add", "--business", "Business B", "--contains", "SHIP", "--direction", "in", "--category", "Sales")
        self.assertIn("SHIP", self.ok("rules", "export", "--business", "Business B"))


def quietly_open(path: Path) -> sqlite3.Connection:
    """Open old books without the upgrade notice on the test output."""
    with contextlib.redirect_stderr(io.StringIO()):
        return db.connect(path)


class Upgrades(BooksCase):
    def test_v1_rules_become_conditions_and_book_the_same_rows(self) -> None:
        old = self.dir / "old.sqlite3"
        make_v1(old)
        raw = sqlite3.connect(str(old))
        legacy = raw.execute("SELECT id, match_type, pattern, direction, priority FROM rules ORDER BY id").fetchall()
        before = raw.execute("SELECT id, category_id, rule_id FROM transactions ORDER BY id").fetchall()
        raw.close()
        conn = quietly_open(old)
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], db.schema_version())
        for rule_id, match_type, pattern, direction, priority in legacy:
            rule = rules.get_rule(conn, rule_id)
            self.assertEqual((rule.match_type, rule.pattern, rule.direction, rule.priority), (match_type, pattern, direction, priority))
            self.assertTrue(rule.enabled)
            self.assertEqual(rule.note, "")
        for business_id in (1, 2):
            outcome = rules.apply_rules(conn, business_id, recheck=True)
            self.assertEqual((outcome["changed"], outcome["returned_to_review"]), (0, 0))
        after = [tuple(row) for row in conn.execute("SELECT id, category_id, rule_id FROM transactions ORDER BY id")]
        self.assertEqual(after, before)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions WHERE note_from_rule = 1").fetchone()[0], 0)

    def test_books_from_just_before_the_change_keep_every_kind_of_old_rule(self) -> None:
        old = self.dir / "old.sqlite3"
        make_v1(old)
        raw = sqlite3.connect(str(old), isolation_level=None)
        raw.row_factory = sqlite3.Row
        step = db.MIGRATIONS.index(db._RULE_CONDITIONS_MIGRATION)
        for number in range(1, step):
            db._apply(raw, number)
        self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], step)
        meals = raw.execute("SELECT id FROM categories WHERE business_id = 1 AND code = '6150'").fetchone()[0]
        raw.execute("DELETE FROM rules")
        columns = "id, business_id, account_id, priority, match_type, pattern, direction, min_cents, max_cents, category_id"
        old_rules = [
            (101, 1, 2, 5, "contains", "CAFE", "out", 500, 500, meals),
            (102, 1, None, 6, "starts", "DINER", None, 1000, 5000, meals),
            (103, 1, None, 7, "exact", "SNACK BAR", "in", 200, None, meals),
            (104, 1, None, 8, "regex", r"^BAKE\w+", None, None, 9000, meals),
        ]
        raw.executemany(f"INSERT INTO rules ({columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", old_rules)
        raw.close()
        conn = quietly_open(old)
        self.addCleanup(conn.close)
        found = {
            rule.id: sorted((c.kind, c.text, c.low, c.high, c.account_id) for c in rule.conditions)
            for rule in rules.load_rules(conn, 1)
        }
        self.assertEqual(found, {
            101: [("account", None, None, None, 2), ("amount_equals", None, 500, 500, None), ("contains", "CAFE", None, None, None),
                  ("direction", "out", None, None, None)],
            102: [("amount_between", None, 1000, 5000, None), ("starts_with", "DINER", None, None, None)],
            103: [("amount_at_least", None, 200, None, None), ("direction", "in", None, None, None), ("exact", "SNACK BAR", None, None, None)],
            104: [("amount_at_most", None, None, 9000, None), ("regex", r"^BAKE\w+", None, None, None)],
        })
        cafe = rules.get_rule(conn, 101)
        self.assertTrue(cafe.matches(2, "CAFE 1", "", merchant_key("CAFE 1"), -500))
        self.assertFalse(cafe.matches(1, "CAFE 1", "", merchant_key("CAFE 1"), -500))
        self.assertTrue(rules.get_rule(conn, 104).matches(1, "BAKERY", "", "BAKERY", -8999))

    def test_salvage_turns_old_rules_into_conditions(self) -> None:
        old = self.dir / "old.sqlite3"
        make_v1(old)
        new = self.dir / "saved.sqlite3"
        safety.salvage(old, new)
        conn = db.connect(new)
        self.addCleanup(conn.close)
        missing = conn.execute(
            "SELECT COUNT(*) FROM rules r WHERE NOT EXISTS (SELECT 1 FROM rule_conditions k WHERE k.rule_id = r.id)"
        ).fetchone()[0]
        self.assertEqual(missing, 0)
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0], 10)
        self.assertEqual(rules.get_rule(conn, 9).pattern, r"^NEON\s+ADS$")

    def test_check_finds_a_rule_with_no_conditions(self) -> None:
        self.ok("business", "add", "Business A")
        self.ok("rules", "add", "--contains", "CAFE", "--category", "Meals")
        self.assertEqual(self.run_cli("check")[0], 0)
        self.conn.execute("DELETE FROM rule_conditions")
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("no conditions", out)
