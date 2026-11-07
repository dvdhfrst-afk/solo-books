# Changelog

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
