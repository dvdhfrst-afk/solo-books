"""Payees: the merchants a business pays or is paid by, their names, merged spellings and the contractor flag."""
from __future__ import annotations

from datetime import date

from solo_books import books, history, ledger, payees
from solo_books.errors import BooksError
from solo_books.periods import parse_period
from tests.helpers import BooksCase, parse_csv


class Directory(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)
        self.card = self.account(self.biz, "A Card", "credit_card")
        design = self.category(self.biz, "6030")
        sales = self.category(self.biz, "4000")
        self.design = design
        add = ledger.add_manual
        add(self.conn, self.acct, date(2025, 2, 1), -40000, "PIXEL FOUNDRY", design)
        add(self.conn, self.card, date(2025, 3, 1), -25000, "PIXEL FOUNDRY STUDIO", design)
        add(self.conn, self.acct, date(2025, 3, 9), 5000, "PIXEL FOUNDRY STUDIO", design)
        add(self.conn, self.acct, date(2024, 12, 20), -10000, "PIXEL FOUNDRY", design)
        add(self.conn, self.acct, date(2025, 4, 1), 300000, "HARBOR LANE STUDIO", sales)
        add(self.conn, self.acct, date(2025, 4, 2), -9000, "PIXEL FOUNDRY")
        add(self.conn, self.acct, date(2025, 4, 3), -50000, "OWNER DRAW", self.category(self.biz, "3100"))
        self.other = self.business("Business B")
        add(self.conn, self.account(self.other, "B Checking"), date(2025, 2, 1), -1000, "PIXEL FOUNDRY")

    def biz_id(self) -> int:
        return self.biz["id"]

    def test_every_spelling_starts_as_its_own_payee(self) -> None:
        names = sorted(payee.name for payee in payees.all_payees(self.conn, self.biz_id()))
        self.assertEqual(names, ["HARBOR LANE STUDIO", "OWNER DRAW", "PIXEL FOUNDRY", "PIXEL FOUNDRY STUDIO"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payees").fetchone()[0], 0, "nothing stored until changed")

    def test_list_by_year(self) -> None:
        rows = parse_csv(self.ok("payee", "list", "--business", "Business A", "--format", "csv"))
        pixel = [(row["year"], row["rows"], row["money_out"]) for row in rows if row["payee"] == "PIXEL FOUNDRY"]
        self.assertEqual(pixel, [("2024", "1", "100.00"), ("2025", "2", "490.00")])
        only = parse_csv(self.ok("payee", "list", "--business", "Business A", "--year", "2025", "--format", "csv"))
        self.assertEqual({row["year"] for row in only}, {"2025"})
        out = self.ok("payee", "list")
        self.assertIn("Business B", out)
        self.assertIn("HARBOR LANE STUDIO", out)

    def test_rename_shows_a_name_and_keeps_the_rows(self) -> None:
        out = self.ok("payee", "rename", "PIXEL FOUNDRY", "Pixel Foundry LLC", "--business", "Business A")
        self.assertIn("is now shown as Pixel Foundry LLC", out)
        self.assertEqual(payees.payee_of(self.conn, self.biz_id(), "PIXEL FOUNDRY").name, "Pixel Foundry LLC")
        self.assertEqual(payees.find_payee(self.conn, self.biz_id(), "pixel foundry llc").key, "PIXEL FOUNDRY")
        self.assertEqual(payees.payee_of(self.conn, self.other["id"], "PIXEL FOUNDRY").name, "PIXEL FOUNDRY", "per business")
        rows = self.conn.execute("SELECT DISTINCT description FROM transactions").fetchall()
        self.assertNotIn("Pixel Foundry LLC", [row[0] for row in rows])
        clash = self.fails("payee", "rename", "HARBOR LANE STUDIO", "pixel foundry studio", "--business", "Business A")
        self.assertIn("already", clash)
        self.ok("payee", "rename", "Pixel Foundry LLC", "PIXEL FOUNDRY", "--business", "Business A")
        self.assertIsNone(self.conn.execute("SELECT display_name FROM payees").fetchone()[0])
        self.assertEqual(len(history.list_entries(self.conn, kind="payee-rename")), 2)

    def test_names_are_checked(self) -> None:
        with self.assertRaises(BooksError):
            payees.rename_payee(self.conn, self.biz_id(), "PIXEL FOUNDRY", "   ")
        with self.assertRaises(BooksError):
            payees.rename_payee(self.conn, self.biz_id(), "PIXEL FOUNDRY", "x" * (payees.MAX_PAYEE_NAME + 1))
        self.assertIn("no payee or merchant spelling", self.fails("payee", "show", "NOBODY", "--business", "Business A"))

    def test_merge_and_unmerge(self) -> None:
        self.ok("payee", "set", "PIXEL FOUNDRY STUDIO", "--business", "Business A", "--contractor", "yes")
        out = self.ok("payee", "merge", "PIXEL FOUNDRY STUDIO", "PIXEL FOUNDRY", "--business", "Business A")
        self.assertIn("is now part of payee PIXEL FOUNDRY", out)
        self.assertIn("is now flagged too", out)
        merged = payees.payee_of(self.conn, self.biz_id(), "PIXEL FOUNDRY STUDIO")
        self.assertEqual((merged.key, merged.contractor), ("PIXEL FOUNDRY", True))
        self.assertEqual(sorted(merged.merchants), ["PIXEL FOUNDRY", "PIXEL FOUNDRY STUDIO"])
        self.assertIn("already one payee", self.fails("payee", "merge", "PIXEL FOUNDRY", "PIXEL FOUNDRY STUDIO", "--business", "Business A"))
        shown = self.ok("payee", "show", "PIXEL FOUNDRY", "--business", "Business A")
        self.assertIn("Contractor: yes", shown)
        self.assertIn("PIXEL FOUNDRY STUDIO: 2 row(s)", shown)
        out = self.ok("payee", "unmerge", "PIXEL FOUNDRY STUDIO", "--business", "Business A")
        self.assertIn("is its own payee again", out)
        self.assertIn("that flag came off again", out)
        self.assertFalse(payees.payee_of(self.conn, self.biz_id(), "PIXEL FOUNDRY").contractor)
        self.assertTrue(payees.payee_of(self.conn, self.biz_id(), "PIXEL FOUNDRY STUDIO").contractor)
        self.assertEqual(self.kinds(), ["payee-set", "payee-merge", "payee-unmerge"])
        self.assertIn("nothing to take out", self.fails("payee", "unmerge", "PIXEL FOUNDRY", "--business", "Business A"))

    def kinds(self) -> list:
        return [entry["kind"] for entry in history.list_entries(self.conn, kind="payee")][::-1]

    def test_merging_into_a_merged_payee_reaches_the_top(self) -> None:
        payees.merge_payees(self.conn, self.biz_id(), "PIXEL FOUNDRY STUDIO", "PIXEL FOUNDRY")
        payees.merge_payees(self.conn, self.biz_id(), "HARBOR LANE STUDIO", "PIXEL FOUNDRY STUDIO")
        self.assertEqual(payees.payee_of(self.conn, self.biz_id(), "HARBOR LANE STUDIO").key, "PIXEL FOUNDRY")
        self.assertIn("Spellings merged into it", self.fails("payee", "unmerge", "PIXEL FOUNDRY", "--business", "Business A"))

    def test_a_circle_of_merges_is_found(self) -> None:
        payees.merge_payees(self.conn, self.biz_id(), "PIXEL FOUNDRY STUDIO", "PIXEL FOUNDRY")
        ids = [row[0] for row in self.conn.execute("SELECT id FROM payees ORDER BY id")]
        self.conn.execute("UPDATE payees SET merged_into = ? WHERE id = ?", (ids[1], ids[0]))
        with self.assertRaises(BooksError):
            payees.directory(self.conn, self.biz_id())
        self.assertTrue(payees.problems(self.conn))
        self.assertEqual(self.run_cli("check")[0], 1)

    def test_flag_contractor(self) -> None:
        out = self.ok("payee", "set", "PIXEL FOUNDRY", "--business", "Business A", "--contractor", "yes")
        self.assertIn("PIXEL FOUNDRY is flagged as a contractor.", out)
        self.ok("payee", "set", "PIXEL FOUNDRY", "--business", "Business A", "--contractor", "no")
        self.assertFalse(payees.payee_of(self.conn, self.biz_id(), "PIXEL FOUNDRY").contractor)
        self.assertEqual(self.run_cli("payee", "set", "PIXEL FOUNDRY", "--contractor", "maybe")[0], 2)

    def test_payee_report(self) -> None:
        report = payees.payee_report(self.conn, self.biz, parse_period("2025"))
        self.assertEqual((report.waiting_count, report.waiting_out, report.left_out_count), (1, 9000, 1))
        top_out = payees.top(report, "out", 10)
        self.assertEqual([(e.payee.name, e.money_out, e.by_card) for e in top_out],
                         [("PIXEL FOUNDRY", 40000, 0), ("PIXEL FOUNDRY STUDIO", 25000, 25000)])
        self.assertEqual([e.payee.name for e in payees.top(report, "in", 1)], ["HARBOR LANE STUDIO"])
        out = self.ok("report", "payees", "--year", "2025", "--business", "Business A")
        self.assertIn("1 transfer and owner row(s)", out)
        self.assertIn("WARNING: 1 uncategorized row(s) are NOT in these figures (in 0.00, out 90.00)", out)
        rows = parse_csv(self.ok("report", "payees", "--year", "2025", "--business", "Business A", "--format", "csv", "--limit", "1"))
        self.assertEqual([row["payee"] for row in rows if row["section"] == "money out"], ["PIXEL FOUNDRY"])

    def test_contractor_report(self) -> None:
        payees.merge_payees(self.conn, self.biz_id(), "PIXEL FOUNDRY STUDIO", "PIXEL FOUNDRY")
        payees.set_contractor(self.conn, self.biz_id(), "PIXEL FOUNDRY", True)
        payees.set_contractor(self.conn, self.biz_id(), "HARBOR LANE STUDIO", True)
        report = payees.contractor_report(self.conn, self.biz, parse_period("2025"), 60000)
        self.assertEqual([(e.payee.name, e.net_paid, e.by_card) for e in report.at_or_over], [("PIXEL FOUNDRY", 60000, 25000)])
        self.assertEqual([e.payee.name for e in report.under], ["HARBOR LANE STUDIO"])
        self.assertEqual((report.waiting_count, report.waiting_out), (1, 9000))
        out = self.ok("report", "contractors", "--year", "2025", "--business", "Business A")
        self.assertIn("It is not a determination of who needs", out)
        self.assertIn("Net paid of 600.00 or more: 1 payee(s)", out)
        self.assertIn("NOT counted", out)
        higher = self.ok("report", "contractors", "--year", "2025", "--business", "Business A", "--threshold", "600.01")
        self.assertIn("or more: 0 payee(s)", higher)
        rows = parse_csv(self.ok("report", "contractors", "--year", "2025", "--business", "Business A", "--format", "csv"))
        over = [row for row in rows if row["at_or_over"] == "yes"]
        self.assertEqual((over[0]["paid"], over[0]["refunded"], over[0]["net_paid"]), ("650.00", "50.00", "600.00"))
        notes = [row["payee"] for row in rows if row["at_or_over"] == "note"]
        self.assertTrue(any("not a determination" in note for note in notes))
        self.assertEqual(self.run_cli("report", "contractors", "--year", "2025", "--threshold", "-1.00")[0], 2)

    def test_no_contractors_says_so(self) -> None:
        out = self.ok("report", "contractors", "--year", "2025", "--business", "Business B")
        self.assertIn("No payee of this business is flagged", out)

    def test_the_directory_never_holds_tax_ids_or_addresses(self) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(payees)")}
        self.assertEqual(columns, {"id", "business_id", "merchant", "display_name", "contractor", "merged_into"})

    def test_deleting_the_business_takes_its_payees(self) -> None:
        payees.set_contractor(self.conn, self.other["id"], "PIXEL FOUNDRY", True)
        books.delete_account(self.conn, books.get_account(self.conn, "B Checking"), with_rows=True)
        books.delete_business(self.conn, books.get_business(self.conn, "Business B"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM payees").fetchone()[0], 0)

    def test_help(self) -> None:
        for command in ("list", "show", "rename", "merge", "unmerge", "set"):
            self.assertIn("usage:", self.ok("payee", command, "--help"))
        for command in ("payees", "contractors"):
            self.assertIn("usage:", self.ok("report", command, "--help"))
