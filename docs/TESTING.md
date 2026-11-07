# What has been tested

Everything here was run on invented data. **This program has never been run on a real person's books, and no real bank
export has ever passed through it.** Nobody outside its own development has used it, and it has had no outside review of
its accounting or its security. Weigh it accordingly: keep backups, and tie your accounts to your statements.

## The automated tests

```bash
python -m unittest discover
```

The suite has 406 tests and needs no network. Results:

| System | Python | Result |
|---|---|---|
| Windows 11 | 3.9.25 | All pass, 1 skipped (owner-only file modes do not exist on Windows). About 60 seconds. |
| Linux in a container (`python:3.9-slim`), as an ordinary user | 3.9.25 | All pass, none skipped. About 45 seconds. |

No other Python version has been run, and macOS has not been run. The code uses nothing specific to any of them, and
the syntax is checked against 3.9, but that is reasoning rather than a test.

What the figures are checked against: `tests/fixtures/worked/WORKED.md` was added up by hand, and
`samples/expected-2025.json` comes from the script that invented the sample year. Neither came from this program.

## A long session

`tests/long_session.py` plays out years of bookkeeping and compares the books with what its generator knows: six
accounts through five different layouts plus OFX, in UTF-8, Windows-1252 and UTF-16, with overlapping exports, a
statement tied and locked every month, hand-booked rows, an import undone, a real process killed during an import, a
backup restored with the later months replayed, and a salvage at the end. Every category for every year must match to
the cent.

```bash
python tests/long_session.py --years 6 --rows-per-account-month 230
```

On Windows 11 with Python 3.9.25, that run read 83,808 rows and made a 38.8 MB file. It took 102 seconds, ran 4,756
commands in one process, tied 456 statements, compared 162 figures, and every one matched. A smaller run on Linux (3
years, 11,016 rows) also matched.

## How long things take

Measured on one Windows 11 desktop, with Python 3.9.25, on books of 120,000 invented rows over about six years (a 58.5 MB
file), including about half a second for Python to start. These are one machine's numbers, not a promise.

| Command | Seconds |
|---|---|
| First import of all 120,000 rows | 11.1 |
| The same file again (nothing new) | 4.2 |
| `status`, `review --groups` | about 0.8 |
| `txn list`, `report pnl` for one year, `report balances` | about 0.5 |
| `report preparer` for one year | 0.8 |
| `check` | 1.4 |
| `backup` | 1.3 |
| `salvage` | 5.6 |
| `imports undo` of the whole file | 1.3 |

With 99,494 rows waiting for review from 3,980 merchants:

- The web page opened in about 3 seconds in a real browser, drawing 100 merchants at a time.
- Each `r CODE` answer in the terminal took about 2 seconds. The time grows with the size of the queue.

## A stranger's run

Twice, an AI agent that had not written the code and had seen only the README followed it from a fresh clone on Python
3.9, then invented its own messy bank and card files and tried to break things. The first run found two ways to book money
wrongly without noticing. The second found a third: different payees grouped under a shared opening such as WIRE TRANSFER
FROM. All three were fixed and have tests, but the third fix has not had a stranger's run of its own. These were agents,
not people: no human stranger has tried this.

## The review page in a browser

Tested on a built-in Chromium browser against the final code, through real clicks on the page's elements, on invented books
of 238 waiting rows: the grouped queue, no rule offered on a group named only `CHECK`, "Choose a category first", booking with one row unticked and a new
rule (58 booked, the unticked row stayed), Undo (58 back, rule gone), the business filter, a description of
`<img src=x onerror=alert(1)>` shown as plain text with no image created, a wrong or missing key refused with status 403,
and no sideways scrolling at 375 pixels wide. Firefox and Safari have not been tried.

## Hostile and broken input

The tests in `tests/test_failures.py` and `tests/test_safety.py`, and a separate run of about 100 bad commands, confirmed a
plain message and the right exit status for: NUL bytes, 200 KB cells, unfinished quotes, UTF-16 with and without a mark,
Excel, PDF, HTML, QIF and random binary files passed as exports, absurd amounts, dates and ids, escape codes and text
direction overrides, spreadsheet formulas, SQL in names, path tricks in names, regular expressions that could hang, locked
and read-only files, a full disk (simulated, and `/dev/full` on Linux), overwritten and truncated books, a page of random
bytes in the middle of a table, another program's database, and a newer version's file.

Upgrades were tested on a real file written by the first schema (`tests/fixtures/books-schema-v1.sql`): every row
survives, the old file is kept, and a failed step leaves the old version whole.

## What has not been tested

- **Any layout against a real export.** Each of the 22 was written from general knowledge of that institution's file and
  tested only against an invented sample. Some will be wrong, and banks change their files. OFX handling has likewise only
  seen invented files. The dry run and the first reconciliation are there to catch this.
- macOS, other Python versions, and browsers other than Chromium.
- The Schedule C line data beyond what `solo_books/data/schedule_c_<year>.json` records as its source. No tax professional
  has reviewed it. See the note at the top of the README.
- A real power cut or a real failing disk. A killed process and hand-damaged files stand in for them.
- Books on a network drive beyond a single `init` and `check`. A cloud sync folder has not been tried, and is not advised.
- Very long runs of the web page with several browser tabs changing the same rows.
- More than about 120,000 rows.
