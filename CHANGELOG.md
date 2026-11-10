# Changelog

## Unreleased

### Added

- `web` serves a local app: a dashboard and pages for transactions, accounts, imports, rules and reconciling, beside the
  review page, with links between them. It works with the keyboard and on a phone-width screen.
- A JSON API under `/api/v1/` for everything those pages do, documented in `docs/API.md`. It needs the run's key in a
  header, refuses writes from other sites, and gives the command line's own message when it refuses a write.
- A history of every change to the books, written in the same step as the change: when, which command (or the review
  page), the kind of change, and each row before and after. `history list`, `history show` and `history revert`, which
  takes back a booking, a note, a category's name or form line, an added rule or a deleted rule, and refuses when a row
  was changed again since.
- Tags on rows: `tag add`, `tag remove`, `tag list`, `tag rename` and `tag delete`, with `txn list --tag`,
  `report pnl --tag`, and a row's tags in `txn show`.
- A payee directory: `payee list`, `payee show`, `payee rename`, `payee merge`, `payee unmerge` and `payee set
  --contractor`, with `report payees` and `report contractors`. `report preparer` adds the contractors list when any
  payee is flagged.
- Budgets per category per year: `budget set`, `budget list`, `budget delete` and `budget copy`, with `report budget`
  for a year, quarter, month or run of whole months.

### Changed

- `web --open` opens the dashboard. The review page keeps its address, and `web` prints both.
- The books gain tables for the history, tags, payees and budgets. Older files are upgraded on first use, with a copy
  kept beside them.
- `check` looks at the history, tags, payees and budgets too, and `salvage` copies them.
- `--format json`, `html` and `markdown` on every report, next to `table` and `csv`. JSON amounts are whole cents with
  a decimal string beside them, and the shape carries a `format_version`. HTML is one printable page that loads
  nothing. Both escape every value. Each format is one module in `solo_books/formats/`.
- `report cashflow`: money in and out of each account, and what moved it.
- `report trend`: each category's yearly totals side by side, with the change.
- `report categories`: each category's row count, total, average and largest row.
- `export journal` in Ledger, Beancount, QIF and CSV forms. Every transaction adds to zero and the totals tie to
  `report pnl`.
- `export archive` and `import-archive`: the whole books as one JSON file with a SHA-256 of its content, loaded only
  into empty books after every field is checked. Older archive versions are stepped up on load, and a newer one is
  refused.
- `report preparer` also writes `summary.html` and `summary.json`.
- `docs/EXPORTS.md`, describing every format, journal and the archive.

### Changed

- The `table` and `csv` output of `report pnl`, `report balances` and `report schedule-c` is unchanged, byte for
  byte, as are the preparer folder's CSV files.

## 1.0.0

First version meant for other people's real books.

### Added

- Ready-made column layouts for 22 bank and card exports, recognized from the file where its columns allow it. None has
  been checked against a live export: see `docs/BANK-LAYOUTS.md`.
- OFX, QFX and QBO files, which need no mapping.
- `check`, `restore` and `salvage`, plus a recovery guide in `docs/RECOVERY.md`. `status` shows the last backup.
- `business rename`, `business delete`, `account rename`, `account delete`, `txn edit`, `mapping layouts`.
- Mappings for files with no header row, with a column giving the direction of the money, and with chatter of any
  length above the header.
- A `solo-books` command when installed, and `SECURITY.md`, `CONTRIBUTING.md` and `docs/TESTING.md`.

### Changed

- Books are marked as solo-books files. Older files are upgraded on first use, with a copy kept beside them.
- Exit status 1 now always means a finding, 2 a problem with the request or a file, 3 a bug and 130 Ctrl+C.
- `-o` prints the file it wrote.
- The review page needs a per-run key in its address, and draws a long queue a page at a time.
- A long list of row warnings is cut short with a count.
- Dates outside 1900 to 2200 and amounts over 100 billion dollars are refused rather than stored.
- Dollar amounts with `CR` or `DR`, and with Unicode minus signs, are read.

### Fixed

- Another program's SQLite file could get solo-books tables added to it. It is now refused.
- A locked file was reported as "not a solo-books file".
- Malformed files could end in a stack trace: NUL bytes, huge cells, unfinished quotes, UTF-16 without a mark, binary data.
- Absurd numbers on the command line could end in a stack trace.
- `init` on a folder claimed success.
- Two businesses whose names reduce to the same file name overwrote each other's preparer files.
- A report sent to the books file itself could destroy them. Reports now refuse SQLite files.
- A regular expression that could hang was accepted as a rule.
- Terminal escape codes in a description reached the screen.
- Importing was slow in a month with many rows of the same amount range.
- One or two letters at the review prompt could book a row to whichever category started with them, without a word. A
  name start now needs three letters, and every answer prints what it booked.
- A rule made at review or on the web page was a "contains" rule on the merchant, so a short name such as PAYMENT caught
  other payees' rows. It is now an exact rule on the merchant, and `--exact` also compares the merchant name.
- Review grouped different payees that shared an opening such as WIRE TRANSFER FROM, and a rule on that name kept
  booking other payees' rows. Names now run on to the payee, review lists the rows it is about to book, and no rule is
  offered on a name that only says how the money moved.
- `salvage` could report thousands of lost rows from a small file, and gave up on a file that was merely cut short.
- A bank id repeated within one file now gets a warning.

## 0.x

Development versions: importing, rules, review, reconciliation, profit and loss, balances, the Schedule C rollup and the
preparer export.
