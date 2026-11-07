# Bank and card layouts

A layout is a ready-made column mapping. These are written from general knowledge of how each institution's
download is arranged.

**None of them has been checked against a live export from that institution.** Each one has been tested only against
an invented file in `samples/layouts/`, written to the same description. Banks also change their downloads without
notice. So treat a layout as a good first guess and look at a dry run before you trust it.

## Using one

If you do not know which layout you need, ask:

```bash
python -m solo_books mapping suggest samples/layouts/example-chase-card.csv
```

It prints the mapping it would use and says on the error stream which layout it recognized.

Attach a layout when you add an account, or later:

```bash
python -m solo_books account map "A Checking" --layout us-bank
```

Then look before you import:

```bash
python -m solo_books import "A Checking" samples/layouts/example-us-bank.csv --dry-run
```

Check three things in the dry run:

1. **The sign.** Money in is positive and money out is negative, on bank accounts and on cards alike. A coffee on a
   card must read negative. If everything looks backward, run `account map NAME --invert-amount`.
2. **The dates.** `03/04/2025` is March 4 here. If your download is day first, change it with
   `account map NAME --date-format %d/%m/%Y`.
3. **The row count.** It should match what the bank shows, less any pending rows.

Then import for real, and tie the first statement with `reconcile run`. That is the test that matters: when the
balances tie to the cent, the file was read correctly.

## The layouts

| Layout | For | Date | Text | Amount | Recognized |
|---|---|---|---|---|---|
| `chase-card` | Chase credit card | `Post Date`, `%m/%d/%Y` | `Description` | `Amount` | by columns |
| `chase-checking` | Chase checking or savings | `Posting Date`, `%m/%d/%Y` | `Description` | `Amount` | by columns |
| `bofa-card` | Bank of America credit card | `Posted Date`, `%m/%d/%Y` | `Payee` | `Amount` | by columns |
| `bofa-checking` | Bank of America checking or savings | `Date`, `%m/%d/%Y` | `Description` | `Amount` | by columns |
| `wells-fargo` | Wells Fargo checking, savings or card (no header row) | `Date`, `%m/%d/%Y` | `Description` | `Amount` | by row shape |
| `citi-card` | Citi credit card | `Date`, `%m/%d/%Y` | `Description` | `Debit` out, `Credit` in | by columns |
| `capital-one-card` | Capital One credit card | `Posted Date`, `%Y-%m-%d` | `Description` | `Debit` out, `Credit` in | by columns |
| `capital-one-360` | Capital One 360 checking or savings | `Transaction Date`, `%m/%d/%y` | `Transaction Description` | `Transaction Amount`, with the direction in `Transaction Type` | by columns |
| `discover-card` | Discover credit card | `Post Date`, `%m/%d/%Y` | `Description` | `Amount`, sign flipped | by columns |
| `amex-card` | American Express card (with Card Member column) | `Date`, `%m/%d/%Y` | `Description` | `Amount`, sign flipped | by columns |
| `amex-card-simple` | American Express card (Date, Description, Amount only; never auto-detected) | `Date`, `%m/%d/%Y` | `Description` | `Amount`, sign flipped | no |
| `apple-card` | Apple Card | `Clearing Date`, `%m/%d/%Y` | `Merchant` | `Amount (USD)`, sign flipped | by columns |
| `barclays-card` | Barclays credit card | `Transaction Date`, `%m/%d/%Y` | `Description` | `Amount` | by columns |
| `us-bank` | U.S. Bank checking or card, and others with the same columns | `Date`, `%m/%d/%Y` | `Name` | `Amount` | by columns |
| `ally` | Ally Bank | `Date`, `%Y-%m-%d` | `Description` | `Amount` | by columns |
| `usaa` | USAA | `Date`, `%Y-%m-%d` | `Description` | `Amount` | by columns |
| `navy-federal` | Navy Federal Credit Union | `Posting Date`, `%m/%d/%Y` | `Transaction Description` | `Amount`, with the direction in `Credit Debit Indicator` | by columns |
| `pnc` | PNC Bank | `Date`, `%m/%d/%Y` | `Description` | `Withdrawals` out, `Deposits` in | by columns |
| `schwab-bank` | Charles Schwab Bank | `Date`, `%m/%d/%Y` | `Description` | `Withdrawal` out, `Deposit` in | by columns |
| `mercury` | Mercury business banking | `Date (UTC)`, `%m-%d-%Y` | `Description` | `Amount` | by columns |
| `generic-amount` | Any bank: Date, Description and one signed Amount, month first | `Date`, `%m/%d/%Y` | `Description` | `Amount` | no |
| `generic-debit-credit` | Any bank: Date, Description, Debit and Credit, month first | `Date`, `%m/%d/%Y` | `Description` | `Debit` out, `Credit` in | no |

"Recognized" means `mapping suggest` and the error message from `import` can name the layout from the file itself.
`amex-card-simple` and the two generic layouts are never guessed: three plain columns say nothing about which way the
sign runs, and a wrong guess would flip every row without any error.

## When the file does not fit

If your file's columns differ, the import stops and names the columns it did find. Nothing is saved. You can adjust a
single column with a flag and keep the rest:

```bash
python -m solo_books account map "A Checking" --date-col "Post Date"
```

Or start from your own file. Every field is described in the README under "Column mappings":

```bash
python -m solo_books mapping suggest samples/layouts/example-generic-amount.csv -o my-bank.json
```

## OFX, QFX and QBO downloads

Many banks also offer "Quicken" or "QuickBooks" downloads. Those files need no mapping at all, and each row carries the
bank's own id, which makes re-importing exact. If your bank offers one, it is the better choice. The `<CURDEF>` must be
USD, and a file with more than one account is refused, because a single import goes to a single account.

Older QIF files, Excel workbooks and PDF statements are not read. The import says so and suggests what to download
instead.

## Adding a layout

Layouts live in `solo_books/data/layouts.json`. Add an entry, add an invented sample at
`samples/layouts/example-<id>.csv` holding the same rows as the others, and the tests in `tests/test_layouts.py` will
check it. Never commit a real export.
