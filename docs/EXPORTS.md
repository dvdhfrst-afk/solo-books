# Report formats, journals and archives

Everything solo-books writes for other people and other programs, and the exact shape of each. The examples come from
the sample year in the README, so you can run them and compare.

## Report formats

Every report takes `--format` and `-o FILE`. `--help` on any report lists the formats this copy knows.

| Format | What it is | Byte order mark |
|---|---|---|
| `table` | Aligned text for the terminal. The default. | Yes, when written with `-o` |
| `csv` | One row per figure, for a spreadsheet. | Yes |
| `json` | For other programs. The shape is below. | No |
| `html` | One printable page that loads nothing from anywhere else. | No |
| `markdown` | Markdown tables, for notes and wikis. | No |

The byte order mark is what lets Excel on Windows open a UTF-8 file with the right characters. JSON, HTML and
Markdown readers do not want one, so those formats leave it off.

```bash
python -m solo_books report pnl --period 2025 --format html -o pnl-2025.html
```

```bash
python -m solo_books report cashflow --period 2025 --by quarter --format markdown -o cashflow-2025.md
```

### Text from your bank stays text

Descriptions, memos, notes and names come from bank files and from you, so every format treats them as text only:

- **CSV**: a value that starts with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading `'`, so a
  spreadsheet shows it rather than running it as a formula. Amounts are written by the program and are left alone.
- **HTML**: every value is escaped. The page carries a content security policy that allows its own inline styles and
  nothing else: no scripts, no images, no fonts, no requests. A print stylesheet keeps each table row on one page.
- **Markdown**: characters Markdown would act on, such as `*`, `_`, `|`, `[` and `<`, are escaped with a backslash.
  Line breaks inside a value become spaces, so a row stays one row.
- **Terminal**: characters that could steer the terminal or reorder text on screen are dropped.

### Adding a format

A format is one module in `solo_books/formats/`. It defines a subclass of `Format` from `solo_books/formats/base.py`
with a `name`, a one-line `help`, `byte_order_mark`, and `render(report)`, which returns the text. Add the module to
`_MODULES` in `solo_books/formats/__init__.py` and every report offers it.

`render` is given a `Report`: its `kind` (`pnl`, `balances`, `schedule-c`, `cashflow`, `trend` or `categories`), its
`parts` (one result per business), and the period. Most formats call `layout.document(report)`, which turns any
report into sections of headings, tables and paragraphs, and only decide how those look. That is how the HTML and
Markdown formats work.

## JSON reports

Every JSON report is one object:

```json
{
  "format_version": 1,
  "report": "pnl",
  "generator": "solo-books 1.0.0",
  "note": "Bookkeeping summary, not tax or accounting advice.",
  "period": {"label": "2025-Q1", "start": "2025-01-01", "end": "2025-03-31"},
  "businesses": []
}
```

`format_version` changes only when a field is removed or changes meaning. New fields can be added without changing it,
so a reader should ignore fields it does not know.

**Amounts** are always an object with whole cents and the same amount as a decimal string:

```json
{"cents": 1078656, "amount": "10786.56"}
```

Use `cents` for arithmetic. No amount is ever a floating point number. Figures read the way the text reports read
them: income and expenses positive, returns negative under income, owner money and transfers with the bank's sign
(positive is money in).

**Periods** are `{"label", "start", "end"}`, dates written `YYYY-MM-DD`. A report grouped with `--by` lists its
`buckets`, and every series of `values` has one entry per bucket.

What each report holds in `businesses`:

| Report | Each business holds |
|---|---|
| `pnl` | `categories` (each with `section`, `code`, `name`, `kind`, `values`, `total`), `totals` (`total_income`, `total_cogs`, `gross_profit`, `total_expenses`, `net`), `not_income_or_expense`, `uncategorized` |
| `balances` | `accounts` (`start`, `opened_in_period`, `end`, and `shows`: `held` or `owed`), `money_at_start`, `net_profit`, `not_profit`, `owner_money`, `transfers`, `waiting_for_review`, `money_at_end`, `inventory_end`, `warnings` |
| `schedule-c` | `form` (year, revision, source and date retrieved), `lines` (each with `part`, `line`, `key`, `label`, `amount`, the `categories` behind it and any `note`), `ties_to_profit_and_loss`, `left_off_the_form`, `uncategorized`, `warnings`, `notes` |
| `cashflow` | `accounts`, `all_accounts`, `what_moved_it` (`Income and costs`, `Owner money`, `Transfers`), `waiting_for_review`; each with `money_in`, `money_out`, `net`, `rows` and per bucket figures |
| `trend` | `years`, `categories` and `totals`, each with one value per year, `change` and `change_percent`, plus `uncategorized_rows` per year |
| `categories` | `categories` (each with `rows`, `total`, `average` and the `largest` row: its id, date, amount and description), `waiting_for_review` |

With more than one business, `pnl`, `balances` and `cashflow` also hold `all_businesses`. `change_percent` is a string
such as `"+12.5"`, or `null` when the first year is zero.

## The new reports

**Cash flow** follows the bank rows, not the categories. For each account it shows money in, money out and the net,
which is how far the balance moved. Below the accounts it sorts the same money by what moved it: income and costs,
owner money, transfers, and rows still waiting for review. The parts add up to the accounts, to the cent. On a card,
money in pays down what is owed.

```bash
python -m solo_books report cashflow --period 2025 --business "Business A" --by quarter
```

For Business A in the sample year, the checking account rises by 48,893.61 and the card is owed 230.05 less. Income
and costs bring in 78,123.66, the profit, and owner draws take out 29,000.00.

**Trend** shows each category's total per year side by side, with the change from the first year to the last.
Years are written `2023..2025`, up to 20 at a time.

```bash
python -m solo_books report trend --business "Business B" --years 2024..2025
```

**Categories** shows, for each category, how many rows it has, their total, the average row, and the one row that
moved the most money, with its row number for `txn show`.

```bash
python -m solo_books report categories --period 2025-Q1 --business "Business B"
```

### CSV columns

| Report | Columns |
|---|---|
| `cashflow` | `business`, `period`, `period_start`, `period_end`, `section`, `line`, `money_in`, `money_out`, `net` |
| `trend` | `business`, `section`, `code`, `category`, one column per year, `change`, `change_percent` |
| `categories` | `business`, `period_start`, `period_end`, `code`, `category`, `kind`, `rows`, `total`, `average`, `largest_amount`, `largest_date`, `largest_transaction_id`, `largest_description` |

The CSV columns of `pnl`, `balances` and `schedule-c` are unchanged from earlier versions.

## The preparer folder

`report preparer` writes the CSV files it always has, byte for byte, and two more:

- `summary.html`: the year's profit and loss, Schedule C rollup and balances for every business, on one printable
  page.
- `summary.json`: the same three reports in one object, with `"report": "preparer-summary"`, the `tax_year`, and
  `reports`, a list of three JSON reports in the shape above.

```bash
python -m solo_books report preparer --year 2025 --out preparer-2025
```

## Journals

`export journal` writes the books as a double-entry journal for plain-text accounting programs, or for anyone who
wants every posting in a spreadsheet.

```bash
python -m solo_books export journal --format ledger --period 2025 -o books-2025.ledger
```

```bash
python -m solo_books export journal --format beancount --period 2025 --business "Business B" -o business-b-2025.beancount
```

```bash
python -m solo_books export journal --format qif --period 2025-Q1 -o books-2025-q1.qif
```

```bash
python -m solo_books export journal --format csv --period 2025 -o postings-2025.csv
```

Each bank row becomes one transaction with two postings that add to zero: one to the account, one to its category.

| In the books | In the journal |
|---|---|
| A checking, savings or other account | `Assets:<business>:<account>` |
| A credit card | `Liabilities:<business>:<account>` |
| An income or returns category | `Income:<business>:<category>` |
| A cost of goods sold or expense category | `Expenses:<business>:<category>` |
| An equity category, such as owner draws | `Equity:<business>:<category>` |
| Any transfer category | `Assets:<business>:Transfer clearing` |
| A row waiting for review | `Equity:<business>:Uncategorized` |
| An opening balance | One entry against `Equity:<business>:Opening balances` |

Both sides of a transfer post to the same clearing account, so it nets to zero when both sides are booked. Anything
left in it is a transfer with one side missing, which `report balances` also points out. Income less expenses in the
journal equals net profit in `report pnl` for the same period.

The opening entry is dated the first day of the period and carries what each account held then, so a journal that
starts in the middle of a year still ends on the right balances. An account opened inside the period starts on its
opening date. Rows waiting for review are marked, and the command says how many there are. Review them first and the
journal has no `Uncategorized` account.

**Ledger** (also read by hledger): dates `YYYY/MM/DD`, the row number as the code in parentheses, `*` for a row in a
finished reconciliation and `!` for one waiting for review, `account` lines for every account, amounts as `$-12.34`,
and the memo and note as `;` comments.

**Beancount**: account names are made of letters, digits and hyphens, as Beancount requires, so `Business B` becomes
`Business-B`. Every account is opened on the first day of the period, the row number is kept as `solo_books_id`
metadata, and a `balance` line after the last day checks every bank and card account against what the books say.
Strings are quoted with `"` and `\` escaped.

**QIF**: a category list, then one block per account with type `Bank` or `CCard`. Categories are written
`<business>:<category>`. Transfers, rows waiting for review and the opening balance go to an account in brackets, as
QIF expects. Dates are `MM/DD/YYYY`.

**CSV**: one row per posting, with the columns `entry`, `date`, `transaction_id`, `status`, `business`,
`description`, `memo`, `note`, `account` and `amount`. Rows with the same `entry` add to zero. Notes such as the
not-advice line come last, with an empty `entry`.

Names that would clash once written, such as two categories that differ only in punctuation, are told apart with a
number. Text from bank rows is kept on one line in every format.

## Archives

An archive is the whole of a books file as one JSON file: every business, account, category, rule, import, row,
reconciliation and inventory count, with their ids. Use it to move books between computers or versions of the
program, or to keep a copy you can read without SQLite.

```bash
python -m solo_books export archive -o books-archive.json
```

```bash
python -m solo_books --db new-books.sqlite3 import-archive books-archive.json
```

The file starts like this:

```json
{
  "archive_version": 1,
  "format": "solo-books archive",
  "written_by": "solo-books 1.0.0",
  "exported_at": "2026-01-15T17:04:12Z",
  "sha256": "618fdf528f14ad8c40d1532f3d15a84f72d793cf1426afd65b2c4d80c0101d78",
  "content": {
    "businesses": [
      {"id": 1, "name": "Business A", "activity": "Consulting", "created_at": "2026-01-15 17:01:02"}
    ]
  }
}
```

`content` holds one list per table: `businesses`, `categories`, `accounts`, `imports`, `rules`, `reconciliations`,
`transactions`, `import_rows`, `rule_exceptions`, `deleted_rows`, `inventory_counts` and `meta`. Each entry is one row
with every column, in id order, one row per line. Amounts are whole cents in fields ending `_cents`.

`sha256` is the SHA-256 of `content` written canonically: keys sorted, no spaces between items, and characters outside
ASCII written as themselves in UTF-8. In Python:

```python
hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
```

Exporting the same books twice gives the same file except for `exported_at`, and so does exporting, loading the
archive into new books and exporting again.

### What loading checks

`import-archive` loads into new, empty books only. It never merges. Before it writes anything it checks, and stops
with one plain sentence at the first problem:

- The file is UTF-8 JSON and says it is a solo-books archive.
- It names an archive version this program reads.
- The SHA-256 matches the content, so the file has not been changed or cut short since it was written.
- Every table is there and no others. Every row has exactly the fields its table has.
- Every field is the right kind: ids and counts are whole numbers, amounts are whole cents within range, dates are
  real dates, choices such as an account's kind are ones the program knows, rule patterns compile, and mappings are
  JSON objects.
- Ids are not repeated, and every reference points at a row that exists, in the same business. A row booked to
  another business's category, or locked to a reconciliation that does not cover it, is refused.
- Each row's duplicate key matches its date, amount and description, so re-importing the same bank file afterwards is
  still recognized.

The books file is created only when loading succeeds. If loading fails partway, a file it created is removed, and
books that were already there are left as they were.

### Versions

`archive_version` is 1. A later version of solo-books that changes the tables writes a higher number, and keeps the
steps that turn each older version into the next. Loading an older archive runs those steps in order and says which
version it was read from. An archive with a higher number than this program knows is refused with a message saying it
came from a newer solo-books.
