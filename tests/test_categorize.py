"""Rules, the review queue, and the chart they book to."""
from __future__ import annotations

from datetime import date

from solo_books import books, importer, ledger, review, rules
from solo_books.errors import BooksError
from tests.helpers import BooksCase


class ChartBase(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.biz = self.business()
        self.acct = self.account(self.biz)

    def add(self, description: str, cents: int = -100, day: date = date(2025, 1, 2)) -> int:
        return ledger.add_manual(self.conn, self.acct, day, cents, description)


class Chart(ChartBase):
    def test_every_business_has_a_non_income_transfer_category(self) -> None:
        other = self.business("Business B")
        for business in (self.biz, other):
            transfer = books.find_category(self.conn, business["id"], "Transfer")
            self.assertEqual(transfer["kind"], "transfer")
            self.assertIsNone(transfer["schedule_c_key"])

    def test_charts_are_separate_per_business(self) -> None:
        other = self.business("Business B")
        books.add_category(self.conn, other["id"], "7000", "Widgets", "expense", "48")
        self.assertEqual(self.category(other, "7000")["name"], "Widgets")
        with self.assertRaises(BooksError):
            self.category(self.biz, "7000")

    def test_find_by_code_name_or_unique_start(self) -> None:
        self.assertEqual(self.category(self.biz, "6150")["name"], "Meals")
        self.assertEqual(self.category(self.biz, "meals")["code"], "6150")
        self.assertEqual(self.category(self.biz, "Trans")["code"], "3900")
        with self.assertRaises(BooksError) as caught:
            self.category(self.biz, "Oth")
        self.assertIn("could mean", str(caught.exception))

    def test_booking_to_another_business_is_refused(self) -> None:
        other = self.business("Business B")
        row = self.add("X")
        with self.assertRaises(BooksError):
            ledger.categorize(self.conn, [row], self.category(other, "6150"))

    def test_map_checks_kind_and_refuses_computed_lines(self) -> None:
        meals = self.category(self.biz, "6150")
        books.map_category(self.conn, meals, "24a", 2025)
        self.assertEqual(self.category(self.biz, "6150")["schedule_c_key"], "travel")
        with self.assertRaises(BooksError):
            books.map_category(self.conn, meals, "1", 2025)
        with self.assertRaises(BooksError):
            books.map_category(self.conn, meals, "28", 2025)
        with self.assertRaises(BooksError):
            books.map_category(self.conn, meals, "35", 2025)
        with self.assertRaises(BooksError):
            books.map_category(self.conn, self.category(self.biz, "Transfer"), "48", 2025)

    def test_map_reads_line_numbers_for_the_year_asked(self) -> None:
        interest = self.category(self.biz, "6065")
        books.map_category(self.conn, interest, "16b", 2025)
        self.assertEqual(self.category(self.biz, "6065")["schedule_c_key"], "interest_other")
        books.map_category(self.conn, interest, "16b", 2026)
        self.assertEqual(self.category(self.biz, "6065")["schedule_c_key"], "interest_vehicle_loan")

    def test_retire_keeps_history_and_blocks_new_booking(self) -> None:
        row = self.add("X")
        meals = self.category(self.biz, "6150")
        ledger.categorize(self.conn, [row], meals)
        books.retire_category(self.conn, meals)
        self.assertNotIn("6150", [c["code"] for c in books.list_categories(self.conn, self.biz["id"])])
        other = self.add("Y", day=date(2025, 1, 3))
        with self.assertRaises(BooksError):
            ledger.categorize(self.conn, [other], self.category(self.biz, "6150"))
        self.assertEqual(ledger.get_transaction(self.conn, row)["category_code"], "6150")

    def test_retire_refused_while_a_rule_uses_it(self) -> None:
        meals = self.category(self.biz, "6150")
        rules.add_rule(self.conn, self.biz["id"], "contains", "CAFE", meals)
        with self.assertRaises(BooksError):
            books.retire_category(self.conn, meals)

    def test_add_and_rename_catch_clashes(self) -> None:
        with self.assertRaises(BooksError):
            books.add_category(self.conn, self.biz["id"], "6150", "New", "expense")
        with self.assertRaises(BooksError):
            books.add_category(self.conn, self.biz["id"], "9999", "meals", "expense")
        with self.assertRaises(BooksError):
            books.add_category(self.conn, self.biz["id"], "9999", "Moved money", "transfer", "48")
        with self.assertRaises(BooksError):
            books.rename_category(self.conn, self.category(self.biz, "6150"), "Travel")
        books.rename_category(self.conn, self.category(self.biz, "6150"), "Client meals")
        self.assertEqual(self.category(self.biz, "6150")["name"], "Client meals")


class Rules(ChartBase):
    def rule(self, match_type: str, pattern: str, code: str = "6150", **kwargs) -> int:
        return rules.add_rule(self.conn, self.biz["id"], match_type, pattern, self.category(self.biz, code), **kwargs)

    def hit(self, description: str, cents: int = -100, account_id: int = None, memo: str = ""):
        from solo_books.text import merchant_key

        return rules.first_match(
            rules.load_rules(self.conn, self.biz["id"]),
            account_id if account_id is not None else self.acct["id"],
            description,
            memo,
            merchant_key(description),
            cents,
        )

    def test_match_types(self) -> None:
        self.rule("contains", "fern cafe")
        self.assertIsNotNone(self.hit("SQ *BLUE FERN CAFE 0105"))
        self.assertIsNone(self.hit("FERN BAR"))

    def test_starts_exact_regex(self) -> None:
        self.rule("starts", "ACME")
        self.rule("exact", "INTEREST PAID")
        self.rule("regex", r"^NEON\s+ADS\b")
        self.assertIsNotNone(self.hit("acme widgets"))
        self.assertIsNone(self.hit("THE ACME"))
        self.assertIsNotNone(self.hit("interest  paid"))
        self.assertIsNone(self.hit("INTEREST PAID LATE FEE"))
        self.assertIsNotNone(self.hit("INTEREST PAID 2"))  # The merchant reads as INTEREST PAID.
        self.assertIsNotNone(self.hit("Neon Ads Q1"))

    def test_contains_finds_a_merchant_split_by_a_store_number(self) -> None:
        self.rule("contains", "BLUE FERN CAFE")
        self.assertIsNotNone(self.hit("BLUE FERN CAFE 0423 OGDEN"))

    def test_contains_also_reads_the_memo(self) -> None:
        self.rule("contains", "INVOICE")
        self.assertIsNotNone(self.hit("DEPOSIT", memo="invoice 1001"))

    def test_priority_then_age_decides(self) -> None:
        late_but_first = self.rule("contains", "STORE", "6080", priority=10)
        self.rule("contains", "STORE", "6120", priority=50)
        self.assertEqual(self.hit("STORE").id, late_but_first)

    def test_direction_size_and_account_limits(self) -> None:
        self.rule("contains", "SHOP", "4000", direction="in")
        self.rule("contains", "BIG", "6080", min_cents=10000)
        other = self.account(self.biz, "Other")
        self.rule("contains", "ONLY", "6120", account_id=other["id"])
        self.assertIsNotNone(self.hit("SHOP", 100))
        self.assertIsNone(self.hit("SHOP", -100))
        self.assertIsNone(self.hit("BIG", -9999))
        self.assertIsNotNone(self.hit("BIG", -10000))
        self.assertIsNone(self.hit("ONLY"))
        self.assertIsNotNone(self.hit("ONLY", account_id=other["id"]))

    def test_bad_input_refused(self) -> None:
        with self.assertRaises(BooksError):
            self.rule("regex", "(")
        with self.assertRaises(BooksError):
            self.rule("sounds-like", "X")
        with self.assertRaises(BooksError):
            self.rule("contains", "  ")
        with self.assertRaises(BooksError):
            self.rule("contains", "X", min_cents=5, max_cents=1)
        other = self.business("Business B")
        with self.assertRaises(BooksError):
            rules.add_rule(self.conn, self.biz["id"], "contains", "X", self.category(other, "6150"))

    def test_rules_book_new_rows_on_import(self) -> None:
        self.rule("contains", "COFFEE")
        path = self.write("a.csv", "Date,Description,Amount\n01/02/2025,COFFEE,-4.50\n01/03/2025,MYSTERY,-9.00\n")
        result = importer.import_export(self.conn, self.acct, path)
        self.assertEqual((result.auto_categorized, result.waiting_for_review), (1, 1))
        row = self.conn.execute("SELECT * FROM transactions WHERE description = 'COFFEE'").fetchone()
        self.assertEqual(row["category_source"], "rule")
        self.assertIsNotNone(row["rule_id"])

    def test_apply_never_touches_hand_work(self) -> None:
        row = self.add("COFFEE")
        ledger.categorize(self.conn, [row], self.category(self.biz, "6080"))
        self.rule("contains", "COFFEE")
        outcome = rules.apply_rules(self.conn, self.biz["id"], recheck=True)
        self.assertEqual(outcome, {"categorized": 0, "changed": 0, "returned_to_review": 0})
        self.assertEqual(ledger.get_transaction(self.conn, row)["category_code"], "6080")

    def test_recheck_follows_a_changed_rule_set(self) -> None:
        coffee = self.add("COFFEE")
        first = self.rule("contains", "COFFEE", "6150")
        self.assertEqual(rules.apply_rules(self.conn, self.biz["id"])["categorized"], 1)
        rules.delete_rule(self.conn, first)
        # Deleting a rule alone leaves its rows booked.
        self.assertEqual(ledger.get_transaction(self.conn, coffee)["category_code"], "6150")
        replacement = self.rule("contains", "COFFEE", "6080")
        # A plain apply only looks at the queue; a recheck also moves rows rules booked earlier.
        self.assertEqual(rules.apply_rules(self.conn, self.biz["id"])["changed"], 0)
        self.assertEqual(rules.apply_rules(self.conn, self.biz["id"], recheck=True)["changed"], 1)
        row = ledger.get_transaction(self.conn, coffee)
        self.assertEqual((row["category_code"], row["rule_id"]), ("6080", replacement))

    def test_recheck_sends_a_row_back_when_its_rule_is_gone(self) -> None:
        row = self.add("COFFEE")
        rule = self.rule("contains", "COFFEE")
        rules.apply_rules(self.conn, self.biz["id"])
        # Point the row at a rule that no longer matches.
        self.conn.execute("UPDATE rule_conditions SET text = 'TEA' WHERE rule_id = ?", (rule,))
        outcome = rules.apply_rules(self.conn, self.biz["id"], recheck=True)
        self.assertEqual(outcome["returned_to_review"], 1)
        self.assertIsNone(ledger.get_transaction(self.conn, row)["category_id"])

    def test_export_then_import_round_trip(self) -> None:
        self.rule("contains", "COFFEE", priority=5, direction="out", min_cents=100, max_cents=2000)
        text = rules.export_rules(self.conn, self.biz["id"])
        other = self.business("Business B")
        path = self.write("rules.csv", text)
        self.assertEqual(rules.import_rules(self.conn, other["id"], path), 1)
        copy = rules.list_rules(self.conn, other["id"])[0]
        self.assertEqual(
            (copy["priority"], copy["pattern"], copy["direction"], copy["min_cents"], copy["max_cents"], copy["category_code"]),
            (5, "COFFEE", "out", 100, 2000, "6150"),
        )

    def test_import_is_all_or_nothing_and_names_the_line(self) -> None:
        path = self.write("rules.csv", "match,pattern,category\ncontains,A,6150\ncontains,B,nope\n")
        with self.assertRaises(BooksError) as caught:
            rules.import_rules(self.conn, self.biz["id"], path)
        self.assertIn("line 3", str(caught.exception))
        self.assertEqual(rules.list_rules(self.conn, self.biz["id"]), [])

    def test_import_needs_its_columns(self) -> None:
        with self.assertRaises(BooksError):
            rules.import_rules(self.conn, self.biz["id"], self.write("r.csv", "pattern\nX\n"))


class Queue(ChartBase):
    def test_groups_by_merchant_largest_first(self) -> None:
        self.add("SQ *BLUE FERN CAFE 0105", -100)
        self.add("SQ *BLUE FERN CAFE 0109", -200)
        self.add("NEON ADS", -300)
        groups = review.group_rows(review.queue(self.conn))
        self.assertEqual(groups[0].merchant, "BLUE FERN CAFE")
        self.assertEqual(groups[0].total_cents, -300)
        self.assertEqual(len(groups), 2)

    def test_same_merchant_in_two_businesses_stays_apart(self) -> None:
        other = self.business("Business B")
        other_acct = self.account(other, "B Checking")
        self.add("NEON ADS")
        ledger.add_manual(self.conn, other_acct, date(2025, 1, 2), -100, "NEON ADS")
        self.assertEqual(len(review.group_rows(review.queue(self.conn))), 2)
        self.assertEqual(len(review.queue(self.conn, other["id"])), 1)

    def _answers(self, *answers: str):
        replies = iter(answers)

        def read(_: str) -> str:
            try:
                return next(replies)
            except StopIteration:
                raise EOFError from None

        return read

    def _run(self, *answers: str, business_id: int = None) -> tuple:
        said = []
        totals = review.run_interactive(self.conn, business_id, None, self._answers(*answers), said.append)
        return totals, "\n".join(said)

    def test_interactive_book_skip_and_quit(self) -> None:
        first = self.add("ONE")
        second = self.add("TWO", day=date(2025, 1, 3))
        third = self.add("THREE", day=date(2025, 1, 4))
        totals, _ = self._run("6150", "s", "q")
        self.assertEqual(totals, {"booked": 1, "skipped": 1})
        self.assertEqual(ledger.get_transaction(self.conn, first)["category_code"], "6150")
        self.assertIsNone(ledger.get_transaction(self.conn, second)["category_id"])
        self.assertIsNone(ledger.get_transaction(self.conn, third)["category_id"])

    def test_interactive_transfer_shortcut(self) -> None:
        row = self.add("EXAMPLE CARD PAYMENT")
        self._run("t")
        self.assertEqual(ledger.get_transaction(self.conn, row)["category_kind"], "transfer")

    def test_interactive_all_from_merchant(self) -> None:
        self.add("BLUE FERN CAFE 0105")
        self.add("BLUE FERN CAFE 0109")
        other = self.add("NEON ADS")
        totals, _ = self._run("a Meals", "q")
        self.assertEqual(totals["booked"], 2)
        self.assertIsNone(ledger.get_transaction(self.conn, other)["category_id"])
        self.assertEqual(rules.list_rules(self.conn, self.biz["id"]), [])

    def test_interactive_rule_covers_future_rows(self) -> None:
        self.add("BLUE FERN CAFE 0105")
        self.add("BLUE FERN CAFE 0109")
        totals, said = self._run("r 6150")
        self.assertEqual(totals["booked"], 2)
        self.assertIn("rule added", said)
        path = self.write("a.csv", "Date,Description,Amount\n04/02/2025,BLUE FERN CAFE 0402,-4.50\n")
        result = importer.import_export(self.conn, self.acct, path)
        self.assertEqual(result.auto_categorized, 1)

    def test_interactive_wrong_answer_explains_and_asks_again(self) -> None:
        row = self.add("ONE")
        totals, said = self._run("nonsense", "?", "l", "6150")
        self.assertIn("no category", said)
        self.assertIn("6150  Meals", said)
        self.assertEqual(totals["booked"], 1)
        self.assertEqual(ledger.get_transaction(self.conn, row)["category_code"], "6150")

    def test_interactive_end_of_input_stops_cleanly(self) -> None:
        self.add("ONE")
        totals, _ = self._run()
        self.assertEqual(totals["booked"], 0)

    def test_uncategorize_returns_a_row_to_the_queue(self) -> None:
        row = self.add("ONE")
        ledger.categorize(self.conn, [row], self.category(self.biz, "6150"))
        self.assertEqual(review.queue(self.conn), [])
        ledger.uncategorize(self.conn, [row])
        self.assertEqual(len(review.queue(self.conn)), 1)


class ReviewAnswers(BooksCase):
    """Two ways a slip at the prompt could book money wrongly and silently."""

    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "Chk", "--kind", "checking", "--date-format", "%Y-%m-%d")

    def bring_in(self, name: str, *rows: str) -> None:
        self.ok("import", "Chk", str(self.write(name, "Date,Description,Amount\n" + "\n".join(rows) + "\n")))

    def booked(self) -> list:
        return [row[0] for row in self.conn.execute("SELECT category_id FROM transactions WHERE category_id IS NOT NULL")]

    def test_a_letter_or_two_books_nothing(self) -> None:
        self.bring_in("a.csv", "2025-01-02,CLIENT DEPOSIT,5000.00")
        for answer in ("a", "r", "w", "u", "ad", "a a", "r w"):
            out = self.ok("review", stdin=f"{answer}\nq\n")
            self.assertEqual(self.booked(), [], answer)
            self.assertTrue("too short" in out or "needs a category" in out, f"{answer}: {out}")

    def test_three_letters_book_and_every_answer_says_what_it_did(self) -> None:
        self.bring_in("a.csv", "2025-01-02,CAFE ONE,-1.00", "2025-01-03,TEA TWO,-2.00", "2025-01-04,BANK NOTE,-3.00")
        out = self.ok("review", stdin="Mea\n6120\nt\n")
        self.assertIn("1 row(s) booked to 6150 Meals", out)
        self.assertIn("1 row(s) booked to 6120 Supplies", out)
        self.assertIn("1 row(s) booked to 3900 Transfer", out)

    def test_a_rule_from_review_reaches_no_further_than_the_merchant_shown(self) -> None:
        self.bring_in("a.csv", "2025-01-02,PAYMENT - THANK YOU,500.00", "2025-01-03,PAYMENT - THANK YOU,600.00")
        out = self.ok("review", stdin="r 3900\n")
        self.assertIn("2 row(s) booked to 3900 Transfer", out)
        self.bring_in(
            "b.csv",
            "2025-02-02,ACH CREDIT HARBOR DENTAL PAYMENT INV 77,5000.00",
            "2025-02-03,PAYMENT - THANK YOU,700.00",
        )
        rows = dict(self.conn.execute("SELECT description, category_source FROM transactions WHERE posted_on >= '2025-02-01'").fetchall())
        self.assertIsNone(rows["ACH CREDIT HARBOR DENTAL PAYMENT INV 77"])
        self.assertEqual(rows["PAYMENT - THANK YOU"], "rule")

    def test_an_exact_rule_covers_the_merchant_however_the_bank_decorates_it(self) -> None:
        self.bring_in("a.csv", "2025-01-02,SQ *BLUE FERN CAFE 0115 OGDEN UT,-1.00")
        self.ok("review", stdin="r 6150\n")
        self.bring_in("b.csv", "2025-02-02,BLUE FERN CAFE 0211 PROVO UT,-2.00")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL").fetchone()[0], 0)

    def test_a_command_line_category_needs_three_letters_too(self) -> None:
        self.bring_in("a.csv", "2025-01-02,CAFE,-1.00")
        self.assertIn("too short", self.fails("categorize", "M", "1"))
        self.ok("categorize", "Mea", "1")

    def test_a_bank_id_repeated_in_one_file_is_called_out(self) -> None:
        text = "<OFX><STMTTRN><DTPOSTED>20250102<TRNAMT>-1.00<FITID>SAME<NAME>CAFE</STMTTRN><STMTTRN><DTPOSTED>20250102<TRNAMT>-1.00<FITID>SAME<NAME>CAFE</STMTTRN></OFX>"
        path = self.write("x.ofx", "OFXHEADER:100\n\n" + text)
        self.assertIn("appears more than once", self.ok("import", "Chk", str(path)))


class PayeesStayApart(BooksCase):
    """Rows that share only how the money moved must not share a booking."""

    def setUp(self) -> None:
        super().setUp()
        self.ok("business", "add", "Business A")
        self.ok("account", "add", "Chk", "--kind", "checking", "--date-format", "%Y-%m-%d")

    def bring_in(self, name: str, *rows: str) -> None:
        self.ok("import", "Chk", str(self.write(name, "Date,Description,Amount\n" + "\n".join(rows) + "\n")))

    def category_of(self, description: str):
        return self.conn.execute("SELECT category_id FROM transactions WHERE description = ?", (description,)).fetchone()[0]

    def test_names_run_on_to_the_payee(self) -> None:
        from solo_books.text import merchant_key

        for description, key in (
            ("WIRE TRANSFER FROM BIG CLIENT INC", "WIRE TRANSFER FROM BIG CLIENT INC"),
            ("ZELLE PAYMENT TO JOHN SMITH 88213", "ZELLE PAYMENT TO JOHN SMITH"),
            ("PAYMENT - THANK YOU", "PAYMENT THANK YOU"),
            ("CHECK 1001", "CHECK"),
            ("DEPOSIT - MOBILE", "DEPOSIT MOBILE"),
            ("SQ *BLUE FERN CAFE 0423 OGDEN UT", "BLUE FERN CAFE"),
            ("ACME WIDGETS CO OGDEN UT", "ACME WIDGETS CO"),
        ):
            self.assertEqual(merchant_key(description), key)

    def test_two_payees_by_wire_are_two_decisions(self) -> None:
        client, owner = "WIRE TRANSFER FROM BIG CLIENT INC", "WIRE TRANSFER FROM SOLE OWNER CAPITAL"
        self.bring_in("a.csv", f"2025-01-02,{client},4000.00", f"2025-01-03,{owner},20000.00")
        self.ok("review", stdin="r 4000\nq\n")
        self.assertIsNotNone(self.category_of(client))
        self.assertIsNone(self.category_of(owner))

    def test_no_rule_on_a_name_that_is_only_a_payment_method(self) -> None:
        for name, row in (("c.csv", "CHECK 1001"), ("d.csv", "DEPOSIT - MOBILE")):
            self.bring_in(name, f"2025-01-02,{row},5.00")
            out = self.ok("review", stdin="r 4000\nq\n")
            self.assertIn("how the money moved", out)
            self.assertIsNone(self.category_of(row))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0], 0)
        out = self.ok("review", stdin="a 4000\n4000\n")
        self.assertIn("booked to 4000", out)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL").fetchone()[0], 0)

    def test_the_prompt_lists_what_a_group_answer_would_book(self) -> None:
        self.bring_in("a.csv", *[f"2025-01-{day:02d},CHECK {1000 + day},-{day}.00" for day in range(1, 13)])
        out = self.ok("review", stdin="q\n")
        self.assertIn("12 queued rows from CHECK, total -78.00", out)
        self.assertIn("CHECK 1010", out)
        self.assertNotIn("CHECK 1011", out)
        self.assertIn("...and 2 more", out)

