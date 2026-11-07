# solo-books

Bookkeeping for one person who owns one or more single-member LLCs (limited liability companies) that
report on Schedule C. Bring in bank and card exports, book every row to a category, tie each account to its
statement to the cent, and hand your tax preparer a clean set of files.

It runs on your own computer: one Python program with no dependencies and one SQLite file. Nothing is sent
anywhere. Amounts are stored as whole cents, so totals never drift.

This is a bookkeeping tool. Its reports are bookkeeping summaries, not tax advice.

- Import CSV (comma-separated values) exports, with a column mapping per account. Overlapping exports do not
  duplicate rows.
- Rules book what they can. A review queue, in the terminal or on a local web page, holds the rest.
- A "Transfer" category keeps card payments and moves between accounts out of income and expenses.
- Separate books and a separate chart of accounts per business.
- Reconcile an account for a statement period and see exactly what does not match.
- Profit and loss by period and business, balances that show what moved the money, a Schedule C rollup per business
  per tax year, and CSV files for your preparer.

## Run it

You need Python 3.9 or newer. There is nothing to install. Run every command from this folder.

```bash
python -m solo_books --help
```

Books live in `books.sqlite3` in the current folder. Use `--db FILE` or set the `SOLO_BOOKS_DB` environment
variable to keep them elsewhere. Your books are ignored by git here, and you can copy them safely at any time:

```bash
python -m solo_books backup books-2025-12-31.sqlite3
```

## Try it on the sample year

`samples/` holds a made-up 2025 for two businesses: 336 rows from "Example Bank" and "Example Card". The
commands below do the whole job with them.

### Set up

```bash
python -m solo_books init
```

```bash
python -m solo_books business add "Business A" --activity "Consulting"
```

```bash
python -m solo_books business add "Business B" --activity "Online shop"
```

Each business starts with a chart of accounts already pointed at Schedule C lines. See it with `chart list`.

```bash
python -m solo_books chart list --business "Business A"
```

Add the accounts. Each has its own column mapping, because every bank's export is different. The opening balance
is the balance at the start of the opening date; on a card it is what you owed.

```bash
python -m solo_books account add "A Checking" --business "Business A" --kind checking --institution "Example Bank" --opening-balance 12500.00 --opening-date 2025-01-01 --mapping samples/mappings/a-checking.json
```

```bash
python -m solo_books account add "A Card" --business "Business A" --kind credit_card --institution "Example Card" --opening-balance 425.00 --opening-date 2025-01-01 --mapping samples/mappings/a-card.json
```

```bash
python -m solo_books account add "B Checking" --business "Business B" --kind checking --institution "Example Bank" --opening-balance 3000.00 --opening-date 2025-01-01 --mapping samples/mappings/b-checking.json
```

Load some rules, so imports book themselves as they arrive.

```bash
python -m solo_books rules import samples/rules-business-a.csv --business "Business A"
```

```bash
python -m solo_books rules import samples/rules-business-b.csv --business "Business B"
```

### Import

```bash
python -m solo_books import "A Checking" samples/example-bank-a-checking-2025-01-to-07.csv
```

The next file overlaps June and July. Those rows are recognized and left alone:

```bash
python -m solo_books import "A Checking" samples/example-bank-a-checking-2025-06-to-12.csv
```

```bash
python -m solo_books import "A Card" samples/example-card-a-2025.csv
```

```bash
python -m solo_books import "B Checking" samples/example-bank-b-checking-2025.csv
```

### Review what the rules missed

See the queue by merchant, largest groups first:

```bash
python -m solo_books review --groups
```

Card payments leave checking and arrive on the card. They are neither income nor expense, so they go to Transfer.
Money you pay yourself goes to Owner draws. Both repeat, so make rules for them:

```bash
python -m solo_books rules add --business "Business A" --contains "CARD PAYMENT" --category Transfer
```

```bash
python -m solo_books rules add --business "Business A" --contains "PAYMENT THANK YOU" --category Transfer
```

```bash
python -m solo_books rules add --business "Business A" --contains "OWNER DRAW" --category "Owner draws"
```

```bash
python -m solo_books rules add --business "Business B" --contains "OWNER DRAW" --category "Owner draws"
```

Work through the rest, a row at a time in the terminal. At the first row (a cafe) type `r 6150`: that books every
queued row from that merchant to Meals and makes a rule for the future. At the next merchant (a hardware store)
type `r 6110`. Type `?` at any time for the other answers.

```bash
python -m solo_books review
```

Or do the same on a web page, which shows the whole queue grouped by merchant, so one choice clears a whole group:

```bash
python -m solo_books web --open
```

It listens on 127.0.0.1 only, and Ctrl+C stops it. It has an Undo for the last action.

### Reconcile

Take the balances off the statement as printed. On a card that means what is owed. `samples/statement-balances-2025.csv`
holds every month's figures for the sample accounts.

```bash
python -m solo_books reconcile run "A Checking" --from 2025-03-01 --to 2025-03-31 --begin 20671.70 --end 24990.73
```

That ties to the cent, so lock it. The number comes from the line starting `Reconciliation`:

```bash
python -m solo_books reconcile finish 1
```

The card for the same month does not tie. A statement CSV makes the comparison line by line:

```bash
python -m solo_books reconcile run "A Card" --from 2025-03-01 --to 2025-03-31 --begin 303.60 --end 508.34 --statement samples/example-card-a-2025-03-statement.csv
```

```text
Difference: 15.00. The statement shows more money out (or less in) than the books.

Where to look:
  No single row explains it. Look for a statement line of 15.00 that is not in the books.

Line by line against the statement file:
  On the statement, not in the books:
    2025-03-27        -15.00  CITY PARKING GARAGE

RESULT: DOES NOT MATCH.
```

The export missed a row. Add it by hand. If a later export does include it, it will not be doubled as long as the
date, amount and text match.

```bash
python -m solo_books txn add "A Card" --date 2025-03-27 --amount -15.00 --description "CITY PARKING GARAGE" --category Travel
```

Run the same `reconcile run` again and it ties. Finish it with the number it prints.

### Report

```bash
python -m solo_books report pnl --period 2025 --by quarter
```

```bash
python -m solo_books report schedule-c --year 2025 --business "Business A"
```

Business B sells goods, so its rollup needs the year's inventory counts for cost of goods sold:

```bash
python -m solo_books inventory set --business "Business B" --year 2025 --beginning 1500.00 --ending 2200.00
```

```bash
python -m solo_books report schedule-c --year 2025 --business "Business B"
```

See what each account held at both ends of the year, and what moved the money between them:

```bash
python -m solo_books report balances --period 2025
```

Write everything your preparer needs into a folder: the rollup, the profit and loss by quarter, the balances, and every
row behind them, for each business.

```bash
python -m solo_books report preparer --year 2025 --out preparer-2025
```

To start over, delete `books.sqlite3`.

## Using it on your own books

### Column mappings

Start from a real export. This guesses a mapping and says what to double-check:

```bash
python -m solo_books mapping suggest my-export.csv -o my-bank.json
```

Attach the mapping to the account, then look before you leap. A dry run saves nothing and shows how the first rows
read. Money in is positive and money out is negative, on bank accounts and on cards alike.

```bash
python -m solo_books account map "A Checking" --mapping my-bank.json
```

```bash
python -m solo_books import "A Checking" my-export.csv --dry-run
```

The mapping is a small JSON file. Only the columns your export has need to be named.

| Field | Meaning |
|---|---|
| `date_column` | The date to book by. Use the posted date, since that is what statements go by. |
| `date_format` | How it is written, for example `%m/%d/%Y` or `%Y-%m-%d`. |
| `description_column` | The text rules match against. |
| `amount_column` | One signed amount per row. Or leave it out and name the next two. |
| `debit_column`, `credit_column` | Separate money-out and money-in columns. |
| `invert_amount` | `true` if the export shows charges as positive numbers, as many card exports do. |
| `memo_column` | Extra text. Rules read it too. |
| `reference_column` | The bank's own unique id for a row, if it has one. Makes re-importing exact. |
| `status_column`, `skip_statuses` | Skip rows with these statuses. The default is `["pending"]`. |
| `skip_lines` | Lines of bank chatter above the header row. |
| `delimiter`, `encoding` | Rarely needed. The default is a comma, and UTF-8 falling back to Windows-1252. |

You can set any of these with flags instead, such as `--date-col "Post Date"` or `--invert-amount`. See
`account map --help`.

### What happens on import

A file goes in whole or not at all. If a row cannot be read, nothing is saved and the error lists every bad line.
`--skip-bad` imports the rest. Pending rows, zero amounts and summary lines such as "Total" or "Ending balance" are
skipped and counted. A row with more fields than the header is a bad line, because an amount like 1,064.33 with no
quotes around it would otherwise be misread.

A row is the same row in a later export if it has the same bank id, or the same date, amount and text (ignoring case
and spacing). Two identical rows on one day stay two rows, because two coffees are real, and importing the file again
still leaves two. You get a warning about them, and another if a new row looks like an old one with reworded text,
since that is your call rather than the program's.

If a bank id shows up with a different amount, or a date more than a week away, the import stops and shows both rows.
That usually means the column is a row number rather than a real id, and `account map NAME --no-reference-col` drops it.

Importing a file again after changing its mapping stops too, because it would put new rows on top of the old ones. Run
`imports undo ID` first.

`imports undo ID` takes back the rows only that file put in. A row another imported file also held stays, along with
its category, and the output says how many stayed. `txn delete ID` removes one row and remembers it, so an overlapping
export will not bring it back.

```bash
python -m solo_books imports list
```

### Rules

Rules run in priority order, lowest number first, and the first match wins. Each has one match type:
`--contains`, `--starts`, `--exact` or `--regex`, none of them case-sensitive. A rule can be limited with
`--direction in` or `out`, `--min`, `--max` and `--account`.

```bash
python -m solo_books rules add --business "Business A" --contains "NOTION" --category "Software subscriptions"
```

```bash
python -m solo_books rules test "NOTION LABS SUBSCRIPTION" --business "Business A"
```

```bash
python -m solo_books rules list --business "Business A"
```

Rules never overwrite a row you categorized by hand. `rules apply` runs them over the queue, and `rules apply --recheck`
also re-runs them over rows that rules booked earlier, which is what you want after changing a rule.
`rules export` and `rules import` move a rule set between businesses or into a file you keep.

To see everything about one row, including how it was booked, which files held it and its note:

```bash
python -m solo_books txn show 12
```

To book specific rows by hand, or send them back to the queue:

```bash
python -m solo_books categorize Meals 12 15 18
```

```bash
python -m solo_books categorize --clear 12
```

### The chart of accounts

Categories belong to one business and have a kind: `income`, `returns`, `cogs` (cost of goods sold), `expense`,
`equity` or `transfer`. The last two never reach profit and loss or the form.

```bash
python -m solo_books chart add 6300 "Shared desks" --kind expense --line 20b --business "Business A"
```

```bash
python -m solo_books chart map 6300 --line 48 --business "Business A"
```

```bash
python -m solo_books chart retire 6300 --business "Business A"
```

Line numbers are read against the latest form unless you pass `--year`, because they change: "Other" interest is
16b on the 2025 form and 16c on the 2026 form, where 16b became "Vehicle loan". A category remembers what the line
means rather than its number, so the same chart rolls up correctly in both years.

### Reconciling

`reconcile run` checks the beginning balance against the books and against the last finished statement, then adds up
the period's rows and compares the result with the ending balance. It exits with status 1 if anything is off, and
names single rows that would explain the difference: one the statement lacks, a likely duplicate, a reversed sign, or
one dated just outside the period.

`reconcile finish ID` works only when the balances tie to the cent. It locks the period's rows against deletion
(categories can still change). A row imported into a finished period later gets a warning, and `reconcile show ID`
lists it. `reconcile reopen ID` unlocks the latest finished period.

```bash
python -m solo_books reconcile list
```

```bash
python -m solo_books status
```

## What the reports mean

Books are cash basis on a calendar year: a row counts on the date in the export. A refund reduces the category it is
booked to. Rows still in the queue are left out of every total, and every report says how many there are and how much
money they hold.

**Periods** are written `2025`, `2025-Q2`, `2025-03` or `2025-01-15..2025-02-20`. Leave off `--business` to see each
business and then all of them side by side. Add `--format csv -o FILE` for a file.

**Balances** start from what the accounts held, add profit, owner money, transfers and anything still in the queue, and
arrive at what the accounts hold, to the cent. Transfers should net to zero: anything else means one side of a move is
missing, is booked to something else, or is in another business's account. A period ending December 31 also shows that
year's ending inventory count. `inventory list` shows every count saved. An account with no opening balance is left out
and named in a warning.

**The Schedule C rollup** uses the line names on the official form, kept per tax year in
`solo_books/data/schedule_c_<year>.json` with the source URL, the form revision and the date retrieved. Every report
prints that stamp. It leaves some things to your preparer:

- Line 24b shows the full amount booked to meals, and lines 28, 29 and 31 include all of it. The form's instructions
  limit what is deductible, and your preparer applies that limit.
- Lines 13 and 30 are usually worked out from asset and home records, not bank rows.
- It does not fill in the form or compute any tax. No tax rates appear in this code.

At the bottom it shows how line 31 ties back to profit and loss.

**A new tax year:** when the Internal Revenue Service (IRS) publishes a new Schedule C, copy the latest
`schedule_c_<year>.json`, change the year, source URL and retrieval date, and compare every line with the form. Keep
each `key` the same when a line means the same thing even if its number moves. Until that file exists, reports for that
year stop and say so; `--lines-year` uses an earlier form's lines and labels the output accordingly.

## How the code is laid out

| Module | Job |
|---|---|
| `cli.py` | Commands and arguments. No bookkeeping logic. |
| `db.py` | The SQLite file, its schema and versioned migrations. |
| `books.py` | Businesses, accounts, the chart of accounts, inventory counts. |
| `csvmap.py` | Reading an export through a mapping. |
| `importer.py` | Importing, re-import safety, undo. |
| `rules.py`, `review.py` | Categorization and the queue. |
| `ledger.py` | Finding, adding, deleting and booking rows. |
| `reconcile.py` | Comparing the books with a statement. |
| `reports.py`, `schedule_c.py` | Profit and loss, balances and the rollup. |
| `render.py` | Text tables and CSV. |
| `web.py`, `data/review.html` | The local review page. |
| `money.py`, `periods.py`, `text.py` | Cents, date ranges, description cleanup. |

A positive `amount_cents` puts money into an account or pays down a card. A negative one takes money out or adds to what
a card is owed. Reports turn that into the figures people expect.

## Checks

```bash
python -m unittest discover
```

`./check.sh` does the same on a system with a POSIX shell. The tests need no network and open no windows.

Two sets of tests check the totals against figures that did not come from this program:
`tests/fixtures/worked/WORKED.md` is a small example added up by hand, and `samples/expected-2025.json` comes from the
script that invented the sample year and knows each row's true category.

## License

MIT. See `LICENSE`.
