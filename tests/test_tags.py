"""Tags: labels a person puts on rows, kept per business."""
from __future__ import annotations

from datetime import date

from solo_books import history, ledger, rules, tags
from solo_books.errors import BooksError
from tests.helpers import BooksCase, parse_csv


class Names(BooksCase):
    def test_names_are_cleaned_and_checked(self) -> None:
        self.assertEqual(tags.clean_tag("  Harbor   Lane "), "Harbor Lane")
        for bad in ("", "   ", "a\tb", "a\nb", "a\x00b", "x" * (tags.MAX_TAG + 1)):
            with self.assertRaises(BooksError):
                tags.clean_tag(bad)
        self.assertEqual(tags.clean_tag("x" * tags.MAX_TAG), "x" * tags.MAX_TAG)


class Tagging(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)
        self.meals = self.category(self.biz, "6150")
        self.lunch = ledger.add_manual(self.conn, self.acct, date(2025, 1, 2), -450, "BLUE FERN CAFE", self.meals)
        self.fee = ledger.add_manual(self.conn, self.acct, date(2025, 1, 3), 120000, "HARBOR LANE STUDIO")
        self.other_biz = self.business("Business B")
        self.other_acct = self.account(self.other_biz, "B Checking")
        self.elsewhere = ledger.add_manual(self.conn, self.other_acct, date(2025, 1, 4), -900, "HARDWARE BARN")

    def test_add_makes_the_tag_and_counts_what_was_already_there(self) -> None:
        out = self.ok("tag", "add", "Harbor Lane", str(self.lunch), str(self.fee))
        self.assertIn("Tagged 2 row(s) Harbor Lane (a new tag).", out)
        out = self.ok("tag", "add", "harbor lane", str(self.lunch))
        self.assertIn("Tagged 0 row(s) Harbor Lane.", out)
        self.assertIn("1 row(s) already had it.", out)
        self.assertEqual(tags.tags_of(self.conn, self.lunch), ["Harbor Lane"])
        self.assertEqual(len(history.list_entries(self.conn, kind="tag-add")), 1, "nothing new is not a change")

    def test_one_business_at_a_time(self) -> None:
        err = self.fails("tag", "add", "Mixed", str(self.lunch), str(self.elsewhere))
        self.assertIn(f"transaction {self.elsewhere} is in a different business", err)
        self.assertIn("no transaction 999", self.fails("tag", "add", "Mixed", "999"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0], 0)

    def test_the_same_name_in_two_businesses_is_two_tags(self) -> None:
        self.ok("tag", "add", "Spring", str(self.lunch))
        self.ok("tag", "add", "SPRING", str(self.elsewhere))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0], 2)
        self.assertIn("--business", self.fails("tag", "rename", "Spring", "Summer"))
        self.ok("tag", "rename", "Spring", "Summer", "--business", "Business B")
        self.assertEqual(tags.tags_of(self.conn, self.elsewhere), ["Summer"])
        self.assertEqual(tags.tags_of(self.conn, self.lunch), ["Spring"])

    def test_remove(self) -> None:
        self.ok("tag", "add", "Harbor", str(self.lunch), str(self.fee))
        out = self.ok("tag", "remove", "HARBOR", str(self.lunch), str(self.lunch))
        self.assertIn("Took Harbor off 1 row(s).", out)
        out = self.ok("tag", "remove", "Harbor", str(self.lunch))
        self.assertIn("1 row(s) did not have it.", out)
        self.assertIn("no tag 'Nope'", self.fails("tag", "remove", "Nope", str(self.lunch)))
        entry = history.list_entries(self.conn, kind="tag-remove")[0]
        self.assertEqual(history.entry_rows(self.conn, entry["id"])[0][0], self.lunch)

    def test_rename_refuses_a_clash_whatever_the_capitals(self) -> None:
        self.ok("tag", "add", "Harbor", str(self.lunch))
        self.ok("tag", "add", "Pine", str(self.fee))
        self.assertIn("already a tag 'Pine'", self.fails("tag", "rename", "Harbor", "PINE"))
        self.assertIn("Tag Harbor is now harbor.", self.ok("tag", "rename", "Harbor", "harbor"))
        self.assertIn("Harbor", history.list_entries(self.conn, kind="tag-rename")[0]["summary"])
        self.assertIn("no tag 'Gone' in any business", self.fails("tag", "rename", "Gone", "Here"))

    def test_delete_leaves_the_rows_alone(self) -> None:
        self.ok("tag", "add", "Harbor", str(self.lunch), str(self.fee))
        out = self.ok("tag", "delete", "Harbor")
        self.assertIn("It came off 2 row(s)", out)
        self.assertEqual(tags.tags_of(self.conn, self.lunch), [])
        self.assertEqual(ledger.get_transaction(self.conn, self.lunch)["category_id"], self.meals["id"])
        self.assertEqual(len(history.entry_rows(self.conn, history.list_entries(self.conn, kind="tag-delete")[0]["id"])), 2)

    def test_deleting_a_row_takes_its_tags(self) -> None:
        self.ok("tag", "add", "Harbor", str(self.lunch))
        ledger.delete_transaction(self.conn, self.lunch)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transaction_tags").fetchone()[0], 0)

    def test_list_counts_and_totals(self) -> None:
        self.ok("tag", "add", "Harbor", str(self.lunch), str(self.fee))
        self.ok("tag", "add", "Spring", str(self.elsewhere))
        out = self.ok("tag", "list")
        self.assertIn("2 tag(s)", out)
        self.assertIn("1,200.00", out)
        rows = parse_csv(self.ok("tag", "list", "--format", "csv", "--business", "Business A"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (rows[0]["tag"], rows[0]["rows"], rows[0]["money_in"], rows[0]["money_out"], rows[0]["net"], rows[0]["waiting_for_review"]),
            ("Harbor", "2", "1200.00", "4.50", "1195.50", "1"),
        )
        target = self.dir / "tags.csv"
        self.ok("tag", "list", "--format", "csv", "-o", str(target))
        self.assertTrue(target.read_text(encoding="utf-8-sig").startswith("business,tag,rows"))

    def test_txn_list_and_show(self) -> None:
        self.ok("tag", "add", "Harbor", str(self.fee))
        out = self.ok("txn", "list", "--tag", "harbor")
        self.assertIn("HARBOR LANE STUDIO", out)
        self.assertNotIn("BLUE FERN CAFE", out)
        self.assertRegex(self.ok("txn", "show", str(self.fee)), r"Tags: +Harbor")
        self.assertIn("no tag 'Nope'", self.fails("txn", "list", "--tag", "Nope"))

    def test_pnl_by_tag(self) -> None:
        self.ok("tag", "add", "Harbor", str(self.lunch))
        out = self.ok("report", "pnl", "--period", "2025", "--business", "Business A", "--tag", "Harbor")
        self.assertIn("Only rows tagged Harbor.", out)
        self.assertIn("4.50", out)
        self.assertNotIn("1,200.00", out)
        rows = parse_csv(self.ok("report", "pnl", "--period", "2025", "--business", "Business A", "--tag", "Harbor", "--format", "csv"))
        totals = {row["category"]: row["amount"] for row in rows if row["section"] == "total"}
        self.assertEqual(totals["Total expenses"], "4.50")

    def test_rules_cannot_set_tags(self) -> None:
        self.assertNotIn("tag", rules.add_rule.__code__.co_varnames)
        self.assertNotIn("--tag", self.ok("rules", "add", "--help"))

    def test_check_finds_a_tag_on_another_business_row(self) -> None:
        self.ok("tag", "add", "Harbor", str(self.lunch))
        tag_id = self.conn.execute("SELECT id FROM tags").fetchone()[0]
        self.conn.execute("INSERT INTO transaction_tags (tag_id, transaction_id) VALUES (?, ?)", (tag_id, self.elsewhere))
        self.assertTrue(tags.problems(self.conn))
        self.assertEqual(self.run_cli("check")[0], 1)

    def test_help(self) -> None:
        for command in ("add", "remove", "list", "rename", "delete"):
            self.assertIn("usage:", self.ok("tag", command, "--help"))
