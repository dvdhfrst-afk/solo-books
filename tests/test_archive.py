"""The portable archive: a round trip keeps everything, and a changed, broken or foreign file is refused plainly."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from unittest import mock

from solo_books import archive, db
from tests.helpers import BooksCase, build_worked_books

STAMP_LINE = re.compile(r'^  "exported_at": ".*",$', re.MULTILINE)


class ArchiveCase(BooksCase):
    def export(self, db_path: Path = None, name: str = "books.json") -> Path:
        path = self.dir / name
        args = ["export", "archive", "-o", str(path)]
        if db_path is None:
            self.ok(*args)
        else:
            self.ok("--db", str(db_path), *args)
        return path

    def load(self, archive_path: Path, target: str = "copy.sqlite3"):
        return self.run_cli("--db", str(self.dir / target), "import-archive", str(archive_path))

    def rewrite(self, path: Path, change, rehash: bool = True) -> Path:
        """Change the content of an archive, and by default give it the hash its new content has."""
        document = json.loads(path.read_text(encoding="utf-8"))
        change(document)
        if rehash:
            document["sha256"] = archive.digest(document["content"])
        changed = self.dir / "changed.json"
        changed.write_text(json.dumps(document), encoding="utf-8")
        return changed


class RoundTrip(ArchiveCase):
    def setUp(self) -> None:
        super().setUp()
        build_worked_books(self)
        self.ok("reconcile", "run", "A Checking", "--from", "2025-01-01", "--to", "2025-01-31", "--begin", "5000.00", "--end", "8188.00")
        self.ok("reconcile", "finish", "1")
        row = self.conn.execute("SELECT id FROM transactions WHERE description LIKE '%BLUE FERN%' ORDER BY id").fetchone()
        self.ok("txn", "note", str(row["id"]), "Lunch with a client, \"quoted\" and café")
        self.ok("backup", str(self.dir / "kept.sqlite3"))

    def test_export_import_export_is_the_same_but_for_the_time(self) -> None:
        first = self.export()
        code, out, err = self.load(first)
        self.assertEqual(code, 0, err)
        self.assertIn("SHA-256 matched", out)
        second = self.export(self.dir / "copy.sqlite3", "again.json")
        one, two = first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")
        self.assertNotEqual(one, "")
        self.assertEqual(STAMP_LINE.sub("", one), STAMP_LINE.sub("", two))
        self.assertEqual(len(STAMP_LINE.findall(one)), 1)

    def test_the_copy_reports_and_reconciles_the_same(self) -> None:
        path = self.export()
        self.load(path)
        copy = ("--db", str(self.dir / "copy.sqlite3"))
        for command in (
            ("report", "pnl", "--period", "2025-Q1", "--by", "month"),
            ("report", "schedule-c", "--year", "2025"),
            ("report", "balances", "--period", "2025-Q1"),
            ("reconcile", "show", "1"),
            ("imports", "list"),
            ("rules", "list", "--business", "Business A"),
            ("inventory", "list"),
            ("status",),
        ):
            mine = self.ok(*command).replace(str(self.db_path), "BOOKS")
            theirs = self.ok(*copy, *command).replace(str(self.dir / "copy.sqlite3"), "BOOKS")
            self.assertEqual(mine, theirs, command)
        self.assertIn("RESULT: no problems found", self.ok(*copy, "check"))
        # The copy carries on: a finished period stays locked, and undoing an import still works by its old id.
        locked = self.conn.execute("SELECT id FROM transactions WHERE reconciliation_id = 1").fetchone()["id"]
        self.assertIn("finished reconciliation 1", self.fails(*copy, "txn", "delete", str(locked)))
        self.assertIn("Removed 9 row(s) from import 4", self.ok(*copy, "imports", "undo", "4"))

    def test_every_table_and_field_is_written(self) -> None:
        document = json.loads(self.export().read_text(encoding="utf-8"))
        self.assertEqual(document["archive_version"], 1)
        self.assertEqual(document["format"], "solo-books archive")
        self.assertRegex(document["exported_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(document["sha256"], archive.digest(document["content"]))
        content = document["content"]
        for table in content:
            count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(len(content[table]), count, table)
        self.assertTrue(content["meta"] and content["reconciliations"] and content["inventory_counts"])
        noted = [row for row in content["transactions"] if row["note"]]
        self.assertEqual(noted[0]["note"], "Lunch with a client, \"quoted\" and café")
        mapping = json.loads(content["accounts"][0]["mapping_json"])
        self.assertIn("date_column", mapping)

    def test_the_archive_holds_every_table_and_column_the_books_have(self) -> None:
        # A new table or column needs a new archive version. This fails until the archive is told about it.
        tables = {
            row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        }
        specs = {spec.name: spec for spec in archive.VERSIONS[archive.current_version()].tables}
        self.assertEqual(tables, set(specs))
        for name, spec in specs.items():
            columns = [row["name"] for row in self.conn.execute(f"PRAGMA table_info({name})")]
            self.assertEqual(sorted(columns), sorted(spec.columns), name)


class Refusals(ArchiveCase):
    def setUp(self) -> None:
        super().setUp()
        build_worked_books(self)
        self.path = self.export()

    def refused(self, path: Path, *expected: str) -> str:
        code, out, err = self.load(path)
        self.assertEqual(code, 2, out + err)
        for text in expected:
            self.assertIn(text, err)
        self.assertEqual(err.count("\n"), 1, err)
        self.assertNotIn("Traceback", err)
        self.assertFalse((self.dir / "copy.sqlite3").exists(), "a refused archive left books behind")
        return err

    def test_a_tampered_archive_is_refused(self) -> None:
        def raise_rent(document) -> None:
            document["content"]["transactions"][0]["amount_cents"] -= 1

        tampered = self.rewrite(self.path, raise_rent, rehash=False)
        self.refused(tampered, "does not match the SHA-256 it carries", "Nothing was loaded")

    def test_a_cut_short_or_foreign_file_is_refused(self) -> None:
        text = self.path.read_text(encoding="utf-8")
        cut = self.write("cut.json", text[: len(text) // 2])
        self.refused(cut, "not valid JSON")
        self.refused(self.write("other.json", '{"rows": []}'), "is not a solo-books archive")
        self.refused(self.write("list.json", "[1, 2]"), "is not a solo-books archive")
        self.refused(self.write("hashless.json", '{"format": "solo-books archive", "archive_version": 1, "content": {}}'), "no SHA-256")

    def test_a_newer_archive_version_is_refused_plainly(self) -> None:
        newer = self.rewrite(self.path, lambda d: d.update(archive_version=2))
        self.refused(newer, "archive version 2, from a newer solo-books", "reads archive versions up to 1")
        unknown = self.rewrite(self.path, lambda d: d.update(archive_version=0))
        self.refused(unknown, "archive version 0, which no solo-books has written")
        self.refused(self.rewrite(self.path, lambda d: d.update(archive_version="1")), "does not say which archive version")

    def test_the_first_bad_field_is_named(self) -> None:
        cases = [
            (lambda c: c["transactions"][2].update(amount_cents=12.5), "transactions entry 3 (id 3): amount_cents must be a whole number"),
            (lambda c: c["transactions"][0].update(posted_on="2025-02-30"), "posted_on must be a date written YYYY-MM-DD"),
            (lambda c: c["accounts"][0].update(kind="brokerage"), "kind must be one of checking, savings, credit_card, other"),
            (lambda c: c["accounts"][0].update(mapping_json="{not json"), "mapping_json must hold a JSON object"),
            (lambda c: c["businesses"][0].update(name="  "), "businesses entry 1 (id 1): name must not be blank"),
            (lambda c: c["transactions"][0].update(category_id=999), "category_id 999 points at no entry in categories"),
            (lambda c: c["transactions"][0].update(colour="red"), "has a field 'colour'"),
            (lambda c: c["transactions"][0].pop("memo"), "has no memo"),
            (lambda c: c.pop("rules"), "has no list of rules"),
            (lambda c: c.update(receipts=[]), "a table 'receipts'"),
            (lambda c: c["transactions"].append(dict(c["transactions"][0])), "repeats id"),
            (lambda c: c["inventory_counts"][0].update(tax_year=1700), "tax_year must be a year from 1900"),
            (lambda c: c["transactions"][0].update(amount_cents=10 ** 16), "too large"),
            (lambda c: c["rules"][0].update(match_type="regex", pattern="(a+)+$"), "rules entry 1"),
        ]
        for change, message in cases:
            with self.subTest(message):
                bad = self.rewrite(self.path, lambda d: change(d["content"]))
                self.refused(bad, message)

    def test_rows_must_stay_inside_their_business(self) -> None:
        def cross(content) -> None:
            other = [c for c in content["categories"] if c["business_id"] == 2][0]
            first_a = [t for t in content["transactions"] if t["account_id"] == 1][0]
            first_a["category_id"] = other["id"]

        self.refused(self.rewrite(self.path, lambda d: cross(d["content"])), "booked to another business's category")

        def changed_amount(content) -> None:
            content["transactions"][0]["amount_cents"] += 100

        self.refused(self.rewrite(self.path, lambda d: changed_amount(d["content"])), "natural_key does not match")

        def open_lock(content) -> None:
            content["reconciliations"].append({
                "id": 1, "account_id": 1, "period_start": "2025-01-01", "period_end": "2025-01-31",
                "statement_begin_cents": 500000, "statement_end_cents": 818800, "status": "open",
                "created_at": "2025-02-01 10:00:00", "finished_at": None,
            })
            content["transactions"][0]["reconciliation_id"] = 1

        self.refused(self.rewrite(self.path, lambda d: open_lock(d["content"])), "locked to a reconciliation that does not cover it")

    def test_books_that_hold_anything_are_refused(self) -> None:
        before = self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        code, _, err = self.run_cli("import-archive", str(self.path))
        self.assertEqual(code, 2)
        self.assertIn("already hold 2 business(es)", err)
        self.assertIn("only into new, empty books", err)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], before)

    def test_initialized_empty_books_are_fine(self) -> None:
        target = self.dir / "fresh.sqlite3"
        db.connect(target, create=True).close()
        code, out, err = self.run_cli("--db", str(target), "import-archive", str(self.path))
        self.assertEqual(code, 0, err)
        with sqlite3.connect(str(target)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0], 2)


class EverySection(ArchiveCase):
    """History, tags, payees, budgets and rules with all their parts survive the trip, and each is checked on the way in."""

    def setUp(self) -> None:
        super().setUp()
        build_worked_books(self)
        rows = [r["id"] for r in self.conn.execute("SELECT id FROM transactions ORDER BY id LIMIT 3")]
        self.ok("tag", "add", "Harbor Lane", str(rows[0]), str(rows[1]))
        self.ok("tag", "add", "Spring", str(rows[0]))
        merchants = [r[0] for r in self.conn.execute(
            "SELECT DISTINCT t.merchant FROM transactions t JOIN accounts a ON a.id = t.account_id"
            " WHERE a.business_id = 1 ORDER BY t.merchant LIMIT 2"
        )]
        self.ok("payee", "rename", merchants[0], "First Payee", "--business", "Business A")
        self.ok("payee", "set", "First Payee", "--contractor", "yes", "--business", "Business A")
        self.ok("payee", "merge", merchants[1], "First Payee", "--business", "Business A")
        self.ok("budget", "set", "--business", "Business A", "--year", "2025", "--category", "6150", "--yearly", "1200.00")
        self.ok("budget", "set", "--business", "Business A", "--year", "2025", "--category", "6080", "--monthly", "25.00", "--month", "3")
        self.ok(
            "rules", "add", "--business", "Business A", "--contains", "NEVER SEEN", "--direction", "out", "--min", "5.00",
            "--max", "50.00", "--day-from", "25", "--day-to", "5", "--category", "6150", "--note", "made up",
        )
        newest = self.conn.execute("SELECT MAX(id) FROM rules").fetchone()[0]
        self.ok("rules", "disable", str(newest))
        self.ok("rules", "move", str(newest), "--to", "1")
        self.path = self.export()

    def test_round_trip_keeps_every_section(self) -> None:
        content = json.loads(self.path.read_text(encoding="utf-8"))["content"]
        for table in ("history", "history_rows", "tags", "transaction_tags", "payees", "budgets", "rule_conditions"):
            self.assertTrue(content[table], table)
        rule = [r for r in content["rules"] if r["enabled"] == 0][0]
        self.assertEqual(rule["note"], "made up")
        kinds = sorted(c["kind"] for c in content["rule_conditions"] if c["rule_id"] == rule["id"])
        self.assertEqual(len(kinds), 4, kinds)
        self.assertTrue([p for p in content["payees"] if p["merged_into"] is not None])
        code, _, err = self.load(self.path)
        self.assertEqual(code, 0, err)
        again = self.export(self.dir / "copy.sqlite3", "again.json")
        self.assertEqual(STAMP_LINE.sub("", self.path.read_text(encoding="utf-8")), STAMP_LINE.sub("", again.read_text(encoding="utf-8")))
        copy = ("--db", str(self.dir / "copy.sqlite3"))
        for command in (
            ("tag", "list"),
            ("payee", "list", "--business", "Business A", "--year", "2025"),
            ("budget", "list", "--business", "Business A", "--year", "2025"),
            ("rules", "list", "--business", "Business A"),
            ("history", "list", "--limit", "100"),
            ("report", "contractors", "--year", "2025", "--business", "Business A"),
        ):
            self.assertEqual(self.ok(*command), self.ok(*copy, *command), command)
        self.assertIn("RESULT: no problems found", self.ok(*copy, "check"))

    def test_salvage_copies_every_section(self) -> None:
        tables = [r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        self.conn.close()
        broken = sqlite3.connect(str(self.db_path))
        broken.execute("PRAGMA foreign_keys = OFF")
        lost_rule = broken.execute("SELECT MAX(id) FROM rules").fetchone()[0]
        lost_tag = broken.execute("SELECT MIN(id) FROM tags").fetchone()[0]
        broken.execute("DELETE FROM rules WHERE id = ?", (lost_rule,))
        broken.execute("DELETE FROM tags WHERE id = ?", (lost_tag,))
        broken.commit()
        before = {table: broken.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
        orphans = {
            "rule_conditions": broken.execute("SELECT COUNT(*) FROM rule_conditions WHERE rule_id = ?", (lost_rule,)).fetchone()[0],
            "transaction_tags": broken.execute("SELECT COUNT(*) FROM transaction_tags WHERE tag_id = ?", (lost_tag,)).fetchone()[0],
        }
        broken.close()
        target = self.dir / "saved.sqlite3"
        code, out, _ = self.run_cli("salvage", str(target))
        self.assertIn(code, (0, 1), out)
        saved = sqlite3.connect(str(target))
        after = {table: saved.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
        saved.close()
        self.assertTrue(orphans["rule_conditions"] and orphans["transaction_tags"])
        for table in tables:
            self.assertEqual(after[table], before[table] - orphans.get(table, 0), table)
        self.assertIn("RESULT: no problems found", self.ok("--db", str(target), "check"))

    def test_each_new_section_is_checked(self) -> None:
        def first(content, table, **change):
            content[table][0].update(change)

        def last_rule_conditions_gone(content):
            rule = [r for r in content["rules"] if r["enabled"] == 0][0]
            content["rule_conditions"] = [c for c in content["rule_conditions"] if c["rule_id"] != rule["id"]]

        def merged_into_itself(content):
            payee = content["payees"][0]
            payee["merged_into"] = payee["id"]

        cases = [
            (lambda c: first(c, "tags", name="a" + chr(9) + "b"), "tags entry 1"),
            (lambda c: first(c, "transaction_tags", tag_id=999), "tag_id 999 points at no entry in tags"),
            (lambda c: first(c, "payees", contractor=2), "payees entry 1"),
            (lambda c: first(c, "payees", merged_into=999), "merged_into 999 points at no entry in payees"),
            (merged_into_itself, "merged in a circle"),
            (lambda c: first(c, "budgets", month=13), "budgets entry 1"),
            (lambda c: first(c, "budgets", amount_cents=-5), "budgets entry 1"),
            (lambda c: first(c, "rule_conditions", kind="colour"), "rule_conditions entry 1"),
            (lambda c: first(c, "rule_conditions", rule_id=999), "rule_id 999 points at no entry in rules"),
            (last_rule_conditions_gone, "no conditions"),
            (lambda c: first(c, "rules", enabled=2), "rules entry 1"),
            (lambda c: first(c, "history", kind="forecast"), "history entry 1"),
            (lambda c: first(c, "history_rows", history_id=999), "history_id 999 points at no entry in history"),
            (lambda c: c["transaction_tags"].append(dict(c["transaction_tags"][0])), "transaction_tags"),
        ]
        for change, message in cases:
            with self.subTest(message):
                bad = self.rewrite(self.path, lambda d: change(d["content"]))
                code, out, err = self.load(bad)
                self.assertEqual(code, 2, out + err)
                self.assertIn(message, err)
                self.assertNotIn("Traceback", err)
                self.assertFalse((self.dir / "copy.sqlite3").exists(), "a refused archive left books behind")


class Upgrades(ArchiveCase):
    def test_an_older_version_is_read_by_stepping_it_up(self) -> None:
        build_worked_books(self)
        path = self.export()
        current = archive.VERSIONS[1]
        # A pretend version 0 that had no inventory counts, and the step that brings it to version 1.
        old_tables = tuple(spec for spec in current.tables if spec.name != "inventory_counts")

        def add_inventory(content: dict) -> dict:
            return {**content, "inventory_counts": []}

        def to_version_zero(document) -> None:
            document["archive_version"] = 0
            del document["content"]["inventory_counts"]

        old = self.rewrite(path, to_version_zero)
        versions = {0: archive.ArchiveVersion(0, old_tables, add_inventory), 1: current}
        with mock.patch.dict(archive.VERSIONS, versions, clear=True):
            code, out, err = self.load(old)
        self.assertEqual(code, 0, err)
        self.assertIn("upgraded from archive version 0", out)
        with sqlite3.connect(str(self.dir / "copy.sqlite3")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_counts").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0])

    def test_empty_books_round_trip(self) -> None:
        path = self.export()
        code, _, err = self.load(path)
        self.assertEqual(code, 0, err)
        again = self.export(self.dir / "copy.sqlite3", "again.json")
        self.assertEqual(
            STAMP_LINE.sub("", path.read_text(encoding="utf-8")), STAMP_LINE.sub("", again.read_text(encoding="utf-8"))
        )
