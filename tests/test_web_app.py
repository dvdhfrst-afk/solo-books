"""The local app's pages, the 'web' command, and the sample year driven end to end through the API.

The pages are checked as served, without a browser: what they reference, how they are guarded, and that
their scripts only ever insert text.
"""
from __future__ import annotations

import csv
import json
import re
from html.parser import HTMLParser
from unittest import mock

from solo_books.money import format_cents, parse_cents
from solo_books.web import ReviewServer, pages
from tests.helpers import REPO_ROOT
from tests.web_helpers import ServerCase

SAMPLES = REPO_ROOT / "samples"
EVERY_PAGE = [path for path, _ in pages.NAVIGATION]
# Ways a script could turn text into markup or code.
UNSAFE_SCRIPT = re.compile(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|setAttribute\(\s*[\"']on")


class References(HTMLParser):
    """Every address a page asks the browser to load or follow."""

    def __init__(self) -> None:
        super().__init__()
        self.found = []
        self.tags = []

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append(tag)
        for name, value in attrs:
            if name in ("src", "href", "action", "formaction", "srcset", "poster", "data") and value is not None:
                self.found.append(value)


class Pages(ServerCase):
    def page(self, path: str, key=None):
        status, data, headers = self.raw("GET", f"{path}?key={self.server.token if key is None else key}")
        return status, data.decode("utf-8"), headers

    def test_every_page_opens_with_the_key_and_a_tight_policy(self) -> None:
        for path in EVERY_PAGE:
            status, page, headers = self.page(path)
            self.assertEqual(status, 200, path)
            policy = headers["Content-Security-Policy"]
            self.assertIn("default-src 'none'", policy)
            self.assertIn("form-action 'none'", policy)
            nonce = re.search(r"'nonce-([^']+)'", policy).group(1)
            self.assertEqual(page.count("<script"), page.count(f'<script nonce="{nonce}"'), path)
            self.assertEqual(page.count("<style"), page.count(f'<style nonce="{nonce}"'), path)
            self.assertIsNone(re.search(r"__[A-Z]+__", page), path)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_every_page_needs_the_key(self) -> None:
        for path in EVERY_PAGE:
            self.assertEqual(self.raw("GET", path)[0], 403, path)
            self.assertEqual(self.page(path, key="wrong")[0], 403, path)

    def test_every_page_references_only_local_paths(self) -> None:
        for path in EVERY_PAGE:
            page = self.page(path)[1]
            self.assertNotIn("http://", page, path)
            self.assertNotIn("https://", page, path)
            self.assertNotIn("@import", page, path)
            self.assertNotRegex(page, r"url\(", path)
            parser = References()
            parser.feed(page)
            for target in parser.found:
                self.assertRegex(target, r"^(/|#)", f"{path}: {target}")
                self.assertFalse(target.startswith("//"), f"{path}: {target}")
            for tag in ("iframe", "object", "embed", "link", "img", "base"):
                self.assertNotIn(tag, parser.tags, path)

    def test_every_page_shares_the_navigation(self) -> None:
        for path in EVERY_PAGE:
            page = self.page(path)[1]
            for target in EVERY_PAGE:
                self.assertIn(f'href="{target}?key={self.server.token}"', page, f"{path} links to {target}")
            self.assertIn(f'href="{path}?key={self.server.token}" aria-current="page"', page, path)

    def test_scripts_insert_values_from_the_books_only_as_text(self) -> None:
        for path in EVERY_PAGE:
            page = self.page(path)[1]
            self.assertIsNone(UNSAFE_SCRIPT.search(page), path)
            self.assertNotIn(" onclick=", page, path)
        for source in sorted((REPO_ROOT / "solo_books" / "data" / "web").glob("*.js")):
            self.assertIsNone(UNSAFE_SCRIPT.search(source.read_text(encoding="utf-8")), source.name)

    def test_pages_are_readable_on_a_phone_and_usable_from_the_keyboard(self) -> None:
        for path in EVERY_PAGE:
            page = self.page(path)[1]
            self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', page, path)
            self.assertIn("<h1", page, path)
            self.assertIn('lang="en"', page, path)
        style = (REPO_ROOT / "solo_books" / "data" / "web" / "app.css").read_text(encoding="utf-8")
        self.assertIn(":focus-visible", style)
        self.assertIn("@media (max-width: 560px)", style)
        self.assertIn("overflow-x: auto", style)
        for part in sorted((REPO_ROOT / "solo_books" / "data" / "web").glob("*.html")):
            text = part.read_text(encoding="utf-8")
            # Every field sits inside a label, so it has a name a screen reader can say.
            fields = len(re.findall(r"<(?:input|select|textarea)\b", text))
            labelled = len(re.findall(r"<label[^>]*>[^<]*<(?:input|select|textarea)\b", text))
            self.assertEqual(fields, labelled, part.name)

    def test_the_key_reaches_only_this_page(self) -> None:
        page = self.page("/dashboard")[1]
        self.assertIn(f'<meta name="solo-books-key" content="{self.server.token}">', page)
        _, _, headers = self.page("/dashboard")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_the_web_command_opens_the_dashboard_and_keeps_its_flags(self) -> None:
        with mock.patch.object(ReviewServer, "serve_forever", side_effect=KeyboardInterrupt), mock.patch("webbrowser.open") as opened:
            out = self.ok("web", "--port", "0", "--open", "--verbose")
        self.assertRegex(out, r"Books at http://127\.0\.0\.1:\d+/dashboard\?key=\S+")
        self.assertRegex(out, r"Review queue at http://127\.0\.0\.1:\d+/\?key=\S+")
        self.assertIn("docs/API.md", out)
        self.assertIn("/dashboard?key=", opened.call_args[0][0])
        self.assertIn("--port", self.ok("web", "--help"))


class SampleYearThroughTheApi(ServerCase):
    """The README's walkthrough with every step after setup done over HTTP, against the generator's own figures."""

    def setUp(self) -> None:
        super().setUp()
        self.expected = json.loads((SAMPLES / "expected-2025.json").read_text(encoding="utf-8"))
        self.ok("business", "add", "Business A", "--activity", "Consulting")
        self.ok("business", "add", "Business B", "--activity", "Online shop")
        for name, business, kind, opening, mapping in (
            ("A Checking", "Business A", "checking", "12500.00", "a-checking"),
            ("A Card", "Business A", "credit_card", "425.00", "a-card"),
            ("B Checking", "Business B", "checking", "3000.00", "b-checking"),
        ):
            self.ok(
                "account", "add", name, "--business", business, "--kind", kind, "--opening-balance", opening,
                "--opening-date", "2025-01-01", "--mapping", str(SAMPLES / "mappings" / f"{mapping}.json"),
            )
        self.business_ids = {b["name"]: b["id"] for b in self.ok_call("GET", "businesses")["businesses"]}
        self.account_ids = {a["name"]: a["id"] for a in self.ok_call("GET", "accounts")["accounts"]}

    def add_rule(self, business: str, **fields) -> dict:
        return self.ok_call("POST", "rules", dict(fields, business_id=self.business_ids[business]), status=201)

    def upload(self, account: str, name: str, dry_run: bool = False) -> dict:
        query = {"account": self.account_ids[account], "filename": name}
        if dry_run:
            query["dry_run"] = "true"
        return self.ok_call("POST", "imports", query=query, upload=(SAMPLES / name).read_bytes(), status=200 if dry_run else 201)

    def accounts(self) -> dict:
        return {a["name"]: a for a in self.ok_call("GET", "accounts")["accounts"]}

    def test_the_whole_year(self) -> None:
        for business, file_name in (("Business A", "rules-business-a.csv"), ("Business B", "rules-business-b.csv")):
            with open(SAMPLES / file_name, encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    fields = {"match": row["match"], "pattern": row["pattern"], "category": row["category"], "priority": int(row["priority"])}
                    if row.get("direction"):
                        fields["direction"] = row["direction"]
                    self.add_rule(business, **fields)

        dry = self.upload("A Checking", "example-bank-a-checking-2025-01-to-07.csv", dry_run=True)
        self.assertGreater(dry["rows_new"], 0)
        self.assertEqual(self.ok_call("GET", "transactions")["total"], 0)
        self.assertEqual(self.ok_call("GET", "imports")["imports"], [])

        first = self.upload("A Checking", "example-bank-a-checking-2025-01-to-07.csv")
        self.assertEqual(first["rows_new"], dry["rows_new"])
        second = self.upload("A Checking", "example-bank-a-checking-2025-06-to-12.csv")
        self.assertGreater(second["rows_duplicate"], 0)
        self.upload("A Card", "example-card-a-2025.csv")
        self.upload("B Checking", "example-bank-b-checking-2025.csv")
        counts = self.expected["row_counts"]
        rows = {name: account["rows"] for name, account in self.accounts().items()}
        self.assertEqual(rows, {"A Checking": counts["A Checking"], "A Card": counts["A Card export"], "B Checking": counts["B Checking"]})

        self.add_rule("Business A", match="contains", pattern="CARD PAYMENT", category="Transfer")
        self.add_rule("Business A", match="contains", pattern="PAYMENT THANK YOU", category="Transfer")
        self.add_rule("Business A", match="contains", pattern="OWNER DRAW", category="Owner draws")
        self.add_rule("Business B", match="contains", pattern="OWNER DRAW", category="Owner draws")

        # What the rules missed: a cafe goes to Meals and a hardware store to Repairs, as the README books them.
        waiting = self.ok_call("GET", "transactions", query={"uncategorized": "true", "limit": "1000"})["transactions"]
        merchants = sorted({row["merchant"] for row in waiting})
        self.assertEqual(len(merchants), 2, merchants)
        for merchant in merchants:
            category = "6150" if "CAFE" in merchant else "6110"
            self.add_rule("Business A", match="exact", pattern=merchant, category=category)
        self.assertEqual(self.ok_call("GET", "status")["waiting"], 0)

        # The card's March statement shows the parking row the export missed.
        march_card = {"account_id": self.account_ids["A Card"], "from": "2025-03-01", "to": "2025-03-31", "begin": "303.60", "end": "508.34"}
        missing = self.ok_call("POST", "reconciliations", march_card)["reconciliation"]
        self.assertEqual(missing["difference"], "-15.00")
        self.assertIn("Difference: 15.00. The statement shows more money out (or less in) than the books.", missing["difference_says"])
        self.assertTrue(any("statement line of 15.00" in hint for hint in missing["hints"]))
        self.ok_call(
            "POST", "transactions",
            {"account_id": self.account_ids["A Card"], "date": "2025-03-27", "amount": "-15.00", "description": "CITY PARKING GARAGE", "category": "Travel"},
            status=201,
        )
        tied = self.ok_call("POST", "reconciliations", march_card)["reconciliation"]
        self.assertTrue(tied["balanced"])
        self.assertEqual(tied["id"], missing["id"])

        # The README's March figures for A Checking tie to the cent and lock.
        march = {"account_id": self.account_ids["A Checking"], "from": "2025-03-01", "to": "2025-03-31", "begin": "20671.70", "end": "24990.73"}
        report = self.ok_call("POST", "reconciliations", march)["reconciliation"]
        self.assertTrue(report["balanced"])
        self.assertEqual(report["difference_cents"], 0)
        finished = self.ok_call("POST", f"reconciliations/{report['id']}/finish")["reconciliation"]
        self.assertEqual(finished["status"], "finished")
        self.assertEqual(self.accounts()["A Checking"]["reconciled_through"], "2025-03-31")
        locked = self.ok_call("GET", "transactions", query={"account": self.account_ids["A Checking"], "period": "2025-03", "limit": "1"})
        self.assertIn("finished reconciliation", self.refused(409, "DELETE", f"transactions/{locked['transactions'][0]['id']}"))

        # Every category's signed total over the year, against the generator.
        for business, key in (("Business A", "Business A, after adding the missing parking row"), ("Business B", "Business B")):
            for code, signed in self.expected[key]["by_code"].items():
                found = self.ok_call(
                    "GET", "transactions", query={"business": self.business_ids[business], "period": "2025", "category": code, "limit": "1"}
                )
                self.assertEqual(found["net_cents"], parse_cents(signed), f"{business} {code}")

        balances = self.expected["ending_balances"]
        shown = self.accounts()
        for account, owed in (("A Checking", "A Checking"), ("A Card", "A Card owed"), ("B Checking", "B Checking")):
            self.assertEqual(shown[account]["balance_printed"], format_cents(parse_cents(balances[owed])), account)
        self.assertTrue(self.ok_call("GET", "check")["healthy"])
