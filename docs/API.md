# The local HTTP API

`python -m solo_books web` serves the books on your own computer: the pages you use in a browser, and a JSON API under
`/api/v1/` that the pages themselves use. A script of your own can use it too. Everything here was produced from the
sample year in the README, so the figures can be checked against `samples/expected-2025.json`.

The API does what the command line does, through the same code and the same checks. A write the command line refuses,
the API refuses with the same sentence. Every write is kept in the history of changes, as the command line's are, with
`web` as the source. It does not create businesses or accounts, change mappings or settings, back up or restore. Those
stay on the command line. Reports come back as JSON, or as a file for the browser to save, and the API writes nothing
to your disk but the books.

## Starting it

```bash
python -m solo_books web
```

```text
Books at http://127.0.0.1:8765/dashboard?key=Zx3...  (Ctrl+C stops it)
Review queue at http://127.0.0.1:8765/?key=Zx3...
The API is under http://127.0.0.1:8765/api/v1/ with the key in the X-Solo-Books-Token header. See docs/API.md.
```

The key is the part after `key=`. It is made new each time the server starts, and it is the only thing that stands
between your books and any other program on this computer, so treat it like a password while the server runs.

## Rules for every request

- **Address.** The server listens on 127.0.0.1 only. The `Host` header must be `127.0.0.1:PORT` or `localhost:PORT`.
- **Key.** Every request sends the key in the `X-Solo-Books-Token` header. Reads need it as much as writes do.
- **Writes** use `POST` or `DELETE`, never `GET`. A write that a browser marks as coming from another site is refused:
  an `Origin` header must be this server's own address, and a `Sec-Fetch-Site` header must be `same-origin` or `none`.
  A script that sends neither header is fine.
- **Bodies.** A JSON body is sent with `Content-Type: application/json` and must be one JSON object. The one exception is
  an uploaded export file, sent as the raw bytes with `Content-Type: application/octet-stream`. A body with any other
  type is refused, which is what stops a web page from sending a plain form here. A write with nothing to say may send
  no body at all.
- **Size.** A body may be up to 8 MB. Import a larger file with the `import` command.
- **Unknown names.** A query parameter or body field the endpoint does not know is refused, so a typo cannot quietly
  widen a search or skip a setting.
- **Ids** in a path are whole numbers from 1 up. Anything else in that place is a 404.

Every answer is JSON and carries `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and
`Referrer-Policy: no-referrer`. The one exception is a report asked for in another format, which comes back as a file
download (see [Reports](#reports)).

## Money and dates

Amounts go out in two forms: whole cents as a number, such as `"amount_cents": -160000`, and the same figure as text,
such as `"amount": "-1,600.00"`. Positive is money into an account or paid off a card; negative is money out or added to
what a card owes. Fields ending in `_printed` show the figure the way a statement prints it, which on a card is what
is owed: `"balance_cents": -19495` is `"balance_printed": "194.95"`. `printed_as_owed` says when that applies.

Amounts come in as text, such as `"-15.00"`, `"$1,200.50"` or `"(12.34)"`, read exactly as the command line reads them.
A number with fractions of a cent is refused rather than rounded. Dates are `YYYY-MM-DD`. A period is `2025`, `2025-Q2`,
`2025-03` or `2025-01-15..2025-02-20`.

## Errors

Every failure has the same shape:

```json
{"error": {"code": "refused", "message": "an amount of zero does not move a balance"}}
```

| Status | `code` | Meaning |
|---|---|---|
| 400 | `bad_request` | The request is malformed: bad JSON, a wrong type, a missing or unknown field, a wrong `Content-Type`. |
| 403 | `forbidden` | Wrong or missing key, a foreign `Host`, or a write from another site. |
| 404 | `not_found` | No such address, or the id in the path names nothing. |
| 405 | `method_not_allowed` | The address exists but not for this method. The `Allow` header lists the ones it takes. |
| 409 | `conflict` | The books' state refuses the write, such as a row in a finished period or an import already undone. |
| 413 | `too_large` | The body is over 8 MB. |
| 422 | `refused` | A well-formed request the bookkeeping rules refuse, such as a category that does not exist. |
| 500 | `bug` | A bug in this program. Nothing was half done. |
| 503 | `unavailable` | The books cannot be opened: busy, damaged or missing. `GET check` still works on damaged books. |

The message is a plain sentence meant for a person, and it is the same one the command line prints for the same
refusal. Some mention a command line flag, such as `--force`; the API's field of the same name does the same thing.

## Overview

| Method | Path | What it does |
|---|---|---|
| GET | `status` | What `status` shows: last backup, the queue, how far each account is reconciled. |
| GET | `businesses` | Businesses. |
| GET | `businesses/{id}/chart` | A business's chart of accounts, with form lines. |
| GET | `accounts` | Accounts with balances. |
| GET | `accounts/{id}` | One account, with its mapping and reconciliations. |
| GET | `transactions` | Rows, filtered and paged, with a total. |
| GET | `transactions/{id}` | One row, with how it was booked and where it came from. |
| POST | `transactions/{id}/category` | Book a row, or send it back to the queue. |
| POST | `transactions/{id}/note` | Set a row's note. |
| POST | `transactions` | Add a row by hand. |
| DELETE | `transactions/{id}` | Delete a row. |
| GET | `imports` | Past imports. |
| POST | `imports` | Upload an export file, or a dry run of it. |
| POST | `imports/{id}/undo` | Take an import back out. |
| GET | `rules` | Rules in the order they are tried. |
| POST | `rules` | Add a rule and run the rules over the queue. |
| DELETE | `rules/{id}` | Delete a rule. |
| POST | `rules/test` | Which rule a description would hit. |
| POST | `rules/apply` | Run the rules over the queue. |
| POST | `rules/{id}/enable` | Let a disabled rule book rows again. |
| POST | `rules/{id}/disable` | Stop a rule booking rows, keeping its place. |
| POST | `rules/{id}/move` | Put a rule at another place in the order. |
| GET | `rules/explain` | Each rule tried on one row, condition by condition. |
| GET | `tags` | Tags, with their rows and money. |
| POST | `tags/add` | Tag rows. |
| POST | `tags/remove` | Take a tag off rows. |
| GET | `payees` | Payees, with their rows and money per year. |
| POST | `payees/rename` | Show a payee under another name. |
| POST | `payees/merge` | Make one payee another spelling of a second. |
| POST | `payees/unmerge` | Take a spelling back out into a payee of its own. |
| POST | `payees/contractor` | Mark a payee as a contractor, or not. |
| GET | `budgets` | Budgets for a year. |
| POST | `budgets` | Set a budget. |
| POST | `budgets/delete` | Delete a year's budget for a category. |
| GET | `reports` | The reports, their parameters and formats. |
| GET | `reports/{kind}` | A report as JSON, or as a file in another format. |
| GET | `reports/{kind}/document` | A report laid out as headings, tables and paragraphs, for a page to draw. |
| GET | `history` | Changes to the books, newest first. |
| GET | `history/{id}` | One change, row by row. |
| POST | `history/{id}/revert` | Take one change back. |
| GET | `config` | The settings in effect and where each came from. Read only. |
| GET | `reconciliations` | Reconciliations. |
| GET | `reconciliations/{id}` | One reconciliation, worked out again. |
| POST | `reconciliations` | Compare the books with a statement period. |
| POST | `reconciliations/{id}/finish` | Lock a period that ties. |
| POST | `reconciliations/{id}/reopen` | Unlock the latest finished period. |
| GET | `check` | The damage and bookkeeping check. Read only. |

The review page's own addresses, `GET /api/queue`, `POST /api/categorize` and `POST /api/undo`, are older, answer
errors as `{"error": "text"}`, and are not part of this API.

## Status and the check

### GET status

```bash
curl -H "X-Solo-Books-Token: $KEY" http://127.0.0.1:8765/api/v1/status
```

```json
{
  "books": "/home/me/books/books.sqlite3",
  "last_backup": null,
  "waiting": 109,
  "businesses": [
    {"id": 1, "name": "Business A", "waiting": 97, "accounts": [
      {"id": 2, "name": "A Card", "kind": "credit_card", "rows": 131, "latest_row": "2025-12-21", "reconciled_through": null},
      {"id": 1, "name": "A Checking", "kind": "checking", "rows": 101, "latest_row": "2025-12-28", "reconciled_through": null}
    ]},
    {"id": 2, "name": "Business B", "waiting": 12, "accounts": [
      {"id": 3, "name": "B Checking", "kind": "checking", "rows": 104, "latest_row": "2025-12-28", "reconciled_through": null}
    ]}
  ],
  "next": null
}
```

`last_backup` is when the last backup was made, in UTC. `next` says what to do when the books have no business or no
account yet.

### GET check

Looks for damage to the file and for broken bookkeeping, as the `check` command does. It changes nothing, and it
answers even when the books are too damaged to open. (This one was taken after the B Checking import was undone.)

```json
{
  "books": "/home/me/books/books.sqlite3",
  "healthy": true,
  "problems": [],
  "notes": [],
  "counts": {"businesses": 2, "accounts": 3, "rows": 232, "waiting": 84, "rules": 23, "finished": 0},
  "schema_version": 2,
  "reads_up_to": 2
}
```

When `healthy` is false, keep a copy of the file as it is and follow [RECOVERY.md](RECOVERY.md).

## Businesses and accounts

### GET businesses

```json
{"businesses": [
  {"id": 1, "name": "Business A", "activity": "Consulting", "created_at": "2026-11-10 05:37:55", "accounts": 2, "waiting": 97},
  {"id": 2, "name": "Business B", "activity": "Online shop", "created_at": "2026-11-10 05:37:55", "accounts": 1, "waiting": 12}
]}
```

### GET businesses/{id}/chart

| Parameter | Meaning |
|---|---|
| `year` | Show form lines as of this tax year. The default is the latest form. |
| `include_retired` | `true` to list retired categories too. |

```json
{
  "business": {"id": 1, "name": "Business A"},
  "tax_year": 2026,
  "categories": [
    {"id": 3, "code": "3900", "name": "Transfer", "kind": "transfer", "line": null, "line_name": null, "line_key": null, "active": true},
    {"id": 29, "code": "6150", "name": "Meals", "kind": "expense", "line": "24b", "line_name": "Travel and meals: Deductible meals", "line_key": "meals", "active": true}
  ]
}
```

(Shortened: the real answer lists every category.) `line_key` is the stable name of the form line, which keeps its
meaning when a new year's form renumbers it.

### GET accounts

| Parameter | Meaning |
|---|---|
| `business` | Only this business's accounts, by id. |

```json
{"accounts": [
  {
    "id": 1, "name": "A Checking", "business_id": 1, "business": "Business A", "kind": "checking", "institution": "",
    "opening_date": "2025-01-01", "rows": 101, "latest_row": "2025-12-28", "balance_on": "2025-12-28",
    "reconciled_through": null, "has_mapping": true, "printed_as_owed": false,
    "opening_balance_cents": 1250000, "opening_balance": "12,500.00", "opening_balance_printed": "12,500.00",
    "balance_cents": 6139361, "balance": "61,393.61", "balance_printed": "61,393.61"
  }
]}
```

The balance is at the end of the day of the latest row. An account with no opening balance has `null` balances.

### GET accounts/{id}

The same fields, plus `rows_before_opening` (rows dated before the opening date, which the balance leaves out), the
column `mapping` as an object (or `null`), and `reconciliations`:

```json
"reconciliations": [{"id": 2, "from": "2025-03-01", "to": "2025-03-31", "status": "finished", "statement_end_cents": 2499073, "statement_end": "24,990.73", "statement_end_printed": "24,990.73"}]
```

## Transactions

A transaction in any answer looks like this:

```json
{
  "id": 5, "date": "2025-01-10", "account_id": 1, "account": "A Checking", "business_id": 1, "business": "Business A",
  "description": "OWNER DRAW TO PERSONAL", "memo": "", "merchant": "OWNER DRAW TO", "reference": "",
  "category": {"id": 2, "code": "3100", "name": "Owner draws", "kind": "equity"},
  "booked_by": "manual", "rule_id": null, "reconciliation_id": null, "import_id": 1, "note": "", "tags": [],
  "amount_cents": -160000, "amount": "-1,600.00"
}
```

`tags` lists the names of the row's tags, sorted.

`category` is `null` while the row waits for review. `booked_by` is `manual`, `rule` or `null`. `reference` is the
bank's own id for the row, if the export had one. `reconciliation_id` is set once the row's period is finished.

### GET transactions

| Parameter | Meaning |
|---|---|
| `business` | A business id. |
| `account` | An account id. |
| `period` | `2025`, `2025-Q2`, `2025-03` or `2025-01-15..2025-02-20`. |
| `category` | A category code, full name, or the start of a name that only one category has. It belongs to `business`, or to the account's business, or to the only business there is. |
| `uncategorized` | `true` for rows waiting for review. Not together with `category`. |
| `search` | Text found in the description, memo or note. |
| `limit` | Rows per answer, 1 to 1000. The default is 100. |
| `offset` | Rows to skip first. The default is 0. |
| `order` | `oldest` (the default) or `newest`. |

```bash
curl -H "X-Solo-Books-Token: $KEY" "http://127.0.0.1:8765/api/v1/transactions?business=1&uncategorized=true&limit=2"
```

```json
{
  "total": 97, "offset": 0, "limit": 2, "net_cents": -2992302, "net": "-29,923.02",
  "transactions": [
    {"id": 5, "date": "2025-01-10", "description": "OWNER DRAW TO PERSONAL", "amount": "-1,600.00", "category": null},
    {"id": 7, "date": "2025-01-15", "description": "EXAMPLE CARD PAYMENT", "amount": "-425.00", "category": null}
  ]
}
```

(Each row is shortened here.) `total` and `net` cover every matching row, not only the page. After the sample year is
booked, `GET transactions?business=1&period=2025&category=6150` gives `"net": "-823.48"`, the generator's figure for
Meals.

### GET transactions/{id}

A transaction with three more fields: `held_back_from_rules` (rule ids that will never book this row), `imports` (the
files that held it, each `{"id", "file_name"}`, empty for a row added by hand), and `rule` (`{"id", "match", "pattern",
"said"}` for the rule that booked it, or `null`). `said` is the whole rule in words, as `rules list` prints it.

### POST transactions/{id}/category

```json
{"category": "Meals"}
```

Books the row by hand to a category of its own business. Rules never change it afterward. `{"category": null}` sends it
back to the review queue. The field must be sent. The answer is `{"transaction": {...}}` with the detail fields.

Refused with 422: a category that does not exist (`no category 'nope' in this business. See 'chart list'.`), a name too
short to be sure of, or a retired category.

### POST transactions/{id}/note

```json
{"note": "Lunch with a client"}
```

An empty note clears it. Line breaks become spaces. The answer is `{"transaction": {...}}`.

### POST transactions

Adds a row by hand, as `txn add` does. If a later export holds the same date, amount and text, it is not doubled.

```json
{"account_id": 2, "date": "2025-03-27", "amount": "-15.00", "description": "CITY PARKING GARAGE", "category": "Travel", "note": ""}
```

`category` and `note` may be left out. The answer is 201:

```json
{"transaction": {"id": 337, "date": "2025-03-27", "account": "A Card", "amount": "-15.00", "category": {"id": 28, "code": "6140", "name": "Travel", "kind": "expense"}, "booked_by": "manual", "imports": []}, "warnings": []}
```

A date inside a finished reconciliation is added with a warning that the period no longer ties. Refused with 422: an
amount of zero, an amount too large to be real, a date that is not `YYYY-MM-DD`, an empty description, an unknown
account or category.

### DELETE transactions/{id}

Deletes the row and remembers it, so an overlapping export does not bring it back.

```json
{"deleted": 19, "amount": "4,288.78", "description": "CLIENT NORTHWIND INV 1006"}
```

A row in a finished period is refused with 409:

```json
{"error": {"code": "conflict", "message": "transaction 19 is part of finished reconciliation 2. Reopen that reconciliation first."}}
```

## Imports

### GET imports

| Parameter | Meaning |
|---|---|
| `account` | Only this account's imports. |

```json
{"imports": [
  {"id": 2, "account_id": 1, "account": "A Checking", "file_name": "example-bank-a-checking-2025-06-to-12.csv",
   "imported_at": "2026-11-10 05:37:55", "rows_read": 57, "rows_new": 41, "rows_duplicate": 16, "rows_skipped": 0, "undone_at": null}
]}
```

### POST imports

The body is the export file itself, sent as `application/octet-stream`. The parameters go in the query string.

| Parameter | Meaning |
|---|---|
| `account` | The account id. Required. |
| `filename` | The file's name, such as `example-bank-a-checking-2025-01-to-07.csv`. Required. Only its last part is kept, and characters other than letters, digits, spaces and `._-()` become `_`. |
| `dry_run` | `true` to work everything out and save nothing. |
| `skip_bad` | `true` to import the readable rows when some are not. |

```bash
curl -H "X-Solo-Books-Token: $KEY" -H "Content-Type: application/octet-stream" --data-binary @samples/example-bank-a-checking-2025-01-to-07.csv "http://127.0.0.1:8765/api/v1/imports?account=1&filename=example-bank-a-checking-2025-01-to-07.csv&dry_run=true"
```

```json
{
  "dry_run": true, "import_id": null, "account": "A Checking", "file_name": "example-bank-a-checking-2025-01-to-07.csv",
  "rows_read": 60, "rows_new": 60, "rows_duplicate": 0, "rows_deleted_before": 0, "rows_skipped": 0, "rows_bad": 0,
  "booked_by_rules": 46, "waiting_for_review": 14, "earliest": "2025-01-02", "latest": "2025-07-28",
  "samples": [
    {"line": 2, "date": "2025-01-02", "description": "CLIENT HARBOR LANE INV 1002", "amount_cents": 309614, "amount": "3,096.14"},
    {"line": 3, "date": "2025-01-06", "description": "REDWOOD PLAZA RENT", "amount_cents": -85000, "amount": "-850.00"}
  ],
  "warnings": [], "row_warnings": [], "bad_rows": []
}
```

Without `dry_run` the same answer comes back with status 201, `"dry_run": false` and the new `import_id`. `samples`
are the first five rows as read: check that money in is positive. `warnings` are about the whole file, such as a sign
that looks backward or a file already imported; `row_warnings` are about single rows, such as a possible duplicate, and
there can be many. `bad_rows` lists the lines that could not be read.

A file goes in whole or not at all. Unreadable rows, a reused bank id, a file that is really a web page or a
spreadsheet, or a file already imported with another mapping are refused with 422 and the full explanation, and
nothing is saved.

### POST imports/{id}/undo

```json
{"force": false}
```

Takes back the rows only this file put in. A row another live import also held stays.

```json
{"removed": 104, "kept": 0, "kept_in": [], "manual_lost": 0, "import_id": 4}
```

Refused with 409: an import already undone, rows in a finished reconciliation, or rows categorized by hand while
`force` is not `true` (that work would be lost).

## Rules

A rule is a list of conditions that must all hold, a category, and an optional note. [RULES.md](RULES.md) explains
each condition. A rule in any answer, here the first rule of the sample year:

```json
{
  "id": 1, "business_id": 1, "business": "Business A", "place": 1, "priority": 100, "enabled": true,
  "conditions": [{"kind": "starts_with", "text": "CLIENT"}, {"kind": "direction", "direction": "in"}],
  "said": "starts with 'CLIENT' and money in -> 4000 Sales", "conditions_said": "starts with 'CLIENT' and money in",
  "category": {"id": 4, "code": "4000", "name": "Sales"}, "note": "",
  "match": "starts", "pattern": "CLIENT", "direction": "in", "account_id": null, "account": null,
  "rows": 30, "min_cents": null, "min": null, "max_cents": null, "max": null
}
```

`place` is the rule's number in the order it is tried. `conditions` are written exactly as a rule file writes them
(see [Rule files](RULES.md#rule-files)), so one can be copied into the other. `rows` is how many rows the rule has
booked. `match`, `pattern`, `direction`, `account_id`, `min` and `max` give the same conditions in the older single
fields, for scripts written before; `match` and `pattern` are `null` for a rule with no wording condition.

### GET rules

| Parameter | Meaning |
|---|---|
| `business` | Only this business's rules. Without it, every business's, business by business. |

The answer is `{"rules": [...]}`, in the order they are tried, the first match wins. Disabled rules are listed too,
with `"enabled": false`.

### POST rules

```json
{
  "business_id": 1, "category": "6150", "note": "Small coffee runs",
  "conditions": [{"kind": "contains", "text": "COFFEE"}, {"kind": "amount_at_most", "amount": "20.00"}]
}
```

| Field | Meaning |
|---|---|
| `business_id` | Which business. It may be left out when there is only one. |
| `conditions` | The conditions, as a rule file writes them. A condition that names an account gives its name. |
| `category` | Code or name. Required. |
| `note` | A note the rule puts on the rows it books. |
| `priority` | Lower numbers are tried first. The default is 100. |
| `enabled` | `false` adds the rule switched off. The default is `true`. |
| `match`, `pattern` | The older way to give one wording condition: `contains`, `starts`, `exact` or `regex`, and its text. |
| `direction` | `in` or `out`, the older way to limit it to money in or out. |
| `min`, `max` | Only rows of at least, or at most, this size, as text such as `"100.00"`. |
| `account_id` | Only rows in this account, which must belong to the business. |

`conditions` and the older fields can be mixed, and at least one condition is needed. The rule is added and the rules
run over the queue, as `rules add` does. The answer is 201, with the rule and how many rows it booked:

```json
{"rule": {"id": 29, "place": 21, "said": "contains 'COFFEE' and size is at most 20.00 -> 6150 Meals, note 'Small coffee runs'", "rows": 0}, "categorized": 0}
```

(The rule is shortened here.) Refused with 400 when there is no condition at all. Refused with 422: an unknown
condition kind, two wording conditions or two of any one kind, a regular expression that does not compile or could
hang, a minimum above the maximum, an account or category from another business, or a retired category.

### DELETE rules/{id}

```json
{"deleted": 23}
```

Rows the rule booked keep their category.

### POST rules/{id}/enable and rules/{id}/disable

No body. A disabled rule keeps its place but books nothing, and the rows it booked stay booked. Enabling it runs the
rules over the queue, as `rules enable` does. The answer is `{"rule": {...}, "categorized": 0}`.

### POST rules/{id}/move

```json
{"to": 1}
```

Puts the rule at place `to`, counted from 1, or just before another rule with `{"before": RULE_ID}`. The answer is the
new place and the business's rules in their new order: `{"place": 1, "rules": [...]}`. Refused with 422 for a place
past the end or a rule from another business.

### GET rules/explain

| Parameter | Meaning |
|---|---|
| `transaction` | A row id. Required. |

Tries each of the row's business's rules on it, in order, as `rules explain` does:

```json
{
  "transaction_id": 1, "merchant": "CLIENT HARBOR LANE", "booked_by": "rule", "rule_id": 1, "winner": 1,
  "trials": [
    {"place": 1, "rule_id": 1, "enabled": true, "said": "starts with 'CLIENT' and money in -> 4000 Sales",
     "conditions": ["starts with 'CLIENT': yes", "money in: yes (money comes in)"],
     "matched": true, "held_back": false, "verdict": "MATCHES, and books the row"}
  ]
}
```

(One trial is shown.) `winner` is the rule that would book the row now, or `null`. `held_back` is true when the row
was taken out of that rule's reach by a manual booking. A row id that does not exist is a 404.

### POST rules/test

```json
{"business_id": 1, "description": "CLOUDSTACK HOSTING", "amount": "-24.09", "account_id": 2}
```

Only `description` is required. Without an amount, it is tested as 1.00 of money out, so a rule limited by direction or
size may need one.

```json
{"merchant": "CLOUDSTACK HOSTING", "rule": {"id": 10, "match": "contains", "pattern": "CLOUDSTACK", "said": "contains 'CLOUDSTACK' -> 6200 Software subscriptions"}, "category": {"id": 32, "code": "6200", "name": "Software subscriptions", "kind": "expense"}, "tested_amount_cents": -2409, "tested_amount": "-24.09"}
```

No match is an answer, not an error: `rule` and `category` are `null`.

### POST rules/apply

```json
{"business_id": 1, "recheck": true}
```

Both fields may be left out: without `business_id` every business's rules run. `recheck` also re-runs them over rows
rules booked earlier, which is what you want after changing a rule. Rows booked by hand are never touched.

```json
{"results": [{"business_id": 1, "business": "Business A", "categorized": 0, "changed": 0, "returned_to_review": 0}]}
```

## Tags

A tag is a name on rows, such as a client or a project, that the reports can count on its own.

### GET tags

| Parameter | Meaning |
|---|---|
| `business` | Only this business's tags. |

```json
{"tags": [{"id": 1, "business_id": 1, "business": "Business A", "name": "Harbor Lane", "rows": 2, "waiting": 0, "money_in_cents": 309614, "money_in": "3,096.14", "money_out_cents": 2409, "money_out": "24.09"}]}
```

`waiting` counts the tagged rows still waiting for review.

### POST tags/add

```json
{"tag": "Harbor Lane", "ids": [1, 102]}
```

Tags the rows, making the tag in their business if it is new. The rows must all be in one business.

```json
{"tag": "Harbor Lane", "added": 2, "already": 0, "created": true}
```

Refused with 400 when `ids` is not a list of row ids, and with 422 for a row that does not exist or a name that is
empty, longer than 40 characters, or holds a tab or line break.

### POST tags/remove

The same body. The answer is `{"tag": "Harbor Lane", "removed": 1, "not_tagged": 0}`. The tag itself stays, even with
no rows left.

## Payees

A payee is whoever a row's money went to or came from, as the bank names them. Each spelling starts as its own payee.

### GET payees

| Parameter | Meaning |
|---|---|
| `business` | Which business. It may be left out when there is only one. |
| `year` | Only payees with rows in this year, and only this year's figures. |

```json
{
  "business_id": 1, "business": "Business A",
  "payees": [
    {"name": "BLUE FERN CAFE", "key": "BLUE FERN CAFE", "renamed": false, "contractor": false, "spellings": ["BLUE FERN CAFE"],
     "years": [{"year": 2025, "rows": 59, "money_out_cents": 82348, "money_out": "823.48", "money_in_cents": 0, "money_in": "0.00"}]}
  ]
}
```

(One payee is shown.) `key` is the spelling the payee started as. `name` is what it is shown as.

### POST payees/rename

```json
{"business_id": 1, "payee": "PIXEL FOUNDRY CONTRACTOR", "name": "Pixel Foundry"}
```

`payee` is any of the payee's names or spellings. The answer is the payee:

```json
{"payee": {"name": "Pixel Foundry", "key": "PIXEL FOUNDRY CONTRACTOR", "renamed": true, "contractor": false, "spellings": ["PIXEL FOUNDRY CONTRACTOR"]}}
```

### POST payees/merge

```json
{"business_id": 1, "payee": "CLIENT FERNDALE CO", "into": "Pixel Foundry"}
```

Makes the first payee another spelling of the second. The answer is `{"payee": {...}, "contractor_carried": false}`,
where `contractor_carried` says whether a contractor mark moved over from the first. Refused with 422 when the
two are already one payee.

### POST payees/unmerge

```json
{"business_id": 1, "payee": "CLIENT FERNDALE CO"}
```

Takes a spelling back out into a payee of its own. The answer is
`{"payee": {...}, "apart_from": "Pixel Foundry", "contractor_returned": false}`. Refused with 422 when the spelling
was never merged.

### POST payees/contractor

```json
{"business_id": 1, "payee": "Pixel Foundry", "contractor": true}
```

Marks the payee as a contractor for the contractors report, or takes the mark off with `false`. `contractor` must be
`true` or `false`. The answer is `{"payee": {...}}`.

## Budgets

A budget is an amount for an income, cost or expense category in one year, given for the whole year or month by month.

### GET budgets

| Parameter | Meaning |
|---|---|
| `business` | Only this business's budgets. |
| `year` | Only this year's. |

```json
{
  "budgets": [
    {"business": "Business A", "year": 2025, "code": "6150", "name": "Meals", "kind": "expense", "entered_as": "yearly",
     "months": [{"month": 1, "amount_cents": 10000, "amount": "100.00"}],
     "year_total_cents": 120000, "year_total": "1,200.00"}
  ]
}
```

(`months` is shortened; it always has all twelve.) `entered_as` is `yearly` or `monthly`.

### POST budgets

```json
{"business_id": 1, "category": "6150", "year": 2025, "yearly": "1200.00"}
```

| Field | Meaning |
|---|---|
| `business_id` | Which business. It may be left out when there is only one. |
| `category` | Code or name. Required. |
| `year` | Required. |
| `yearly` | The amount for the year, spread evenly over the months. |
| `monthly` | The amount for each month. With `month`, for that month only. |
| `month` | 1 to 12, with `monthly`. |

Give `yearly` or `monthly`, not both. Amounts are positive for income and spending alike. The answer is
`{"budget": {...}, "spread": false}`. `spread` is true when a monthly amount for one month first turned a yearly
budget into twelve monthly ones, as `budget set` does.

### POST budgets/delete

```json
{"business_id": 1, "category": "6080", "year": 2025}
```

```json
{"deleted": {"code": "6080", "name": "Office expense", "year": 2025}, "year_total_cents": 10000, "year_total": "100.00"}
```

## Reports

Every report the `report` command writes, through the same code and the same format registry. The JSON is exactly
what `report KIND --format json` writes, in the shape [EXPORTS.md](EXPORTS.md#json-reports) describes.

### GET reports

```json
{
  "reports": [
    {"report": "pnl", "parameters": ["business", "format", "period", "by", "tag"]},
    {"report": "balances", "parameters": ["business", "format", "period"]},
    {"report": "schedule-c", "parameters": ["business", "format", "year", "lines_year", "all_lines"]},
    {"report": "cashflow", "parameters": ["business", "format", "period", "by", "tag"]},
    {"report": "trend", "parameters": ["business", "format", "years", "tag"]},
    {"report": "categories", "parameters": ["business", "format", "period", "tag"]},
    {"report": "budget", "parameters": ["business", "format", "period", "tag"]},
    {"report": "payees", "parameters": ["business", "format", "year", "limit", "tag"]},
    {"report": "contractors", "parameters": ["business", "format", "year", "threshold"]}
  ],
  "formats": ["table", "csv", "json", "html", "markdown"],
  "taggable": ["pnl", "cashflow", "categories", "trend", "budget", "payees"]
}
```

### GET reports/{kind}

| Parameter | Meaning |
|---|---|
| `business` | Only this business. Without it, every business. |
| `format` | `json` (the default), `csv`, `html`, `markdown` or `table`. |
| `period` | For `pnl`, `balances`, `cashflow`, `categories` and `budget`. Required. |
| `year` | For `schedule-c`, `payees` and `contractors`. Required. |
| `years` | For `trend`, such as `2024..2025`. Required. |
| `by` | `month`, `quarter` or `year`, for `pnl` and `cashflow`. |
| `tag` | Only rows with this tag. |
| `limit` | Payees each way, for `payees`. The default is 10. |
| `threshold` | Where the contractors list divides, such as `"600.00"`. |
| `lines_year`, `all_lines` | For `schedule-c`, as `--lines-year` and `--all-lines`. |

Each matches the `report` command's option of the same name. A parameter the report does not take is refused with
400, as is `tag` on a report that cannot be limited to one.

```bash
curl -H "X-Solo-Books-Token: $KEY" "http://127.0.0.1:8765/api/v1/reports/pnl?business=1&period=2025-Q1"
```

```json
{"format_version": 1, "report": "pnl", "generator": "solo-books 1.0.0", "note": "Bookkeeping summary, not tax or accounting advice.", "period": {"label": "2025-Q1", "start": "2025-01-01", "end": "2025-03-31"}, "tag": null, "businesses": [...]}
```

Any other format comes back as a file to save, byte for byte what `report KIND --format FORMAT -o FILE` writes, with
a `Content-Disposition: attachment` header naming it after the report and period:

```text
HTTP/1.1 200 OK
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="pnl-2025-q1.csv"
```

### GET reports/{kind}/document

The same parameters but `format`. The report laid out the way the HTML and Markdown formats lay it out, for a page to
draw: a title, sections with a heading and intro lines, and blocks that are either a paragraph or a table. Dates follow
the date setting.

```json
{
  "report": "pnl", "title": "Profit and loss",
  "sections": [
    {"heading": "Profit and loss: Business A", "intro": ["Period: 2025-Q1 (2025-01-01 to 2025-03-31), cash basis"],
     "blocks": [
       {"headers": ["", "Total"], "rows": [["INCOME", ""], ["  4000 Sales", "25,970.85"]], "numeric": [1], "styles": ["group", ""]},
       {"paragraph": "...", "warning": true}
     ]}
  ],
  "note": "Bookkeeping summary, not tax or accounting advice."
}
```

(Shortened.) `numeric` lists the columns that hold figures. `styles` marks each row as a `group` heading, a `total`, or
neither.

## History

Every change to the books, from the command line or through the API, as `history` shows it.

### GET history

| Parameter | Meaning |
|---|---|
| `business` | Only this business's changes. |
| `kind` | A kind such as `categorize` or `chart-rename`, or its first word, such as `rule` or `tag`. |
| `period` | By the date of the change, in universal time. |
| `row` | Only changes that touched this row id. |
| `limit` | Changes per answer, 1 to 1000. The default is 100. |

```json
{
  "changes": [
    {"id": 37, "changed_at": "2026-11-10 06:53:43", "source": "web", "kind": "rule-move", "business_id": 1,
     "business": "Business A", "rows": 0, "summary": "Moved rule 29 to number 1 in the order.",
     "reverted_by": null, "revertible": false}
  ]
}
```

`source` is the command that made the change, or `web` for the pages and the API. `reverted_by` is the id of the
change that took this one back. `revertible` says whether `history/{id}/revert` would try.

### GET history/{id}

The change with three more fields: `kind_means` in words, `detail` (what the change recorded beyond rows), and
`row_changes`, each row as it was before and after:

```json
{
  "change": {
    "id": 26, "kind": "tag-add", "summary": "Tagged 2 row(s) Harbor Lane (a new tag).", "kind_means": "rows tagged",
    "detail": {"created": true, "tag": "Harbor Lane", "tag_id": 1},
    "row_changes": [
      {"transaction_id": 1, "before": {"tag": "Harbor Lane", "tagged": false}, "after": {"tag": "Harbor Lane", "tagged": true},
       "before_text": "not tagged Harbor Lane", "after_text": "tagged Harbor Lane"}
    ]
  }
}
```

(Shortened.)

### POST history/{id}/revert

No body. Takes the change back, as `history revert` does: a booking, a note, a category's name or form line, a rule
added or a rule deleted. The answer is `{"reverted": 26, "summary": "...", "change": {...}}`, where `change` is the
new change that did it. Refused with 409, and nothing changed, for any other kind of change, a change already taken
back, or a row changed again since.

## Settings

### GET config

What `config list` shows. Read only: settings are changed with the `config` command.

```json
{
  "settings_file": "/home/me/.config/solo-books/config.json", "settings_file_exists": false,
  "profile": null, "profile_from": null,
  "settings": [
    {"name": "report_format", "value": "table", "shown": "table", "from": "default", "means": "how every report prints"}
  ]
}
```

(One setting is shown.) `from` is `default`, the settings file, the profile, or the environment variable that set it.

## Reconciliations

A reconciliation in any answer:

```json
{
  "id": 1, "account_id": 2, "account": "A Card", "printed_as_owed": true, "from": "2025-03-01", "to": "2025-03-31",
  "status": "open", "finished_at": null, "rows": 12, "balances_tie": false, "balanced": false,
  "difference_says": "Difference: 15.00. The statement shows more money out (or less in) than the books.",
  "prior_id": null,
  "hints": ["No single row explains it. Look for a statement line of 15.00 that is not in the books, or compare line by line with --statement FILE."],
  "notes": [], "rows_added_after_finish": [],
  "statement_begin_cents": -30360, "statement_begin": "-303.60", "statement_begin_printed": "303.60",
  "statement_end_cents": -50834, "statement_end": "-508.34", "statement_end_printed": "508.34",
  "books_end_cents": -49334, "books_end": "-493.34", "books_end_printed": "493.34",
  "books_begin_cents": -30360, "books_begin": "-303.60", "books_begin_printed": "303.60",
  "money_in_cents": 30360, "money_in": "303.60", "money_out_cents": 49334, "money_out": "493.34",
  "difference_cents": -1500, "difference": "-15.00",
  "begin_gap_cents": 0, "begin_gap": "0.00", "prior_gap_cents": null, "prior_gap": null
}
```

`balances_tie` is true when the ending balance, the beginning balance against the books, and the beginning balance
against the last finished statement all agree; only then can it be finished. `hints` name single rows that would
explain a difference: one the statement lacks, a likely duplicate, a reversed sign, or one dated just outside the
period. `rows_added_after_finish` lists rows that arrived inside a finished period. `books_begin` is `null` when the
account has no opening balance.

### GET reconciliations

| Parameter | Meaning |
|---|---|
| `account` | Only this account's. |

```json
{"reconciliations": [
  {"id": 2, "account_id": 1, "account": "A Checking", "from": "2025-03-01", "to": "2025-03-31", "status": "finished", "balanced": true,
   "statement_begin_cents": 2067170, "statement_begin": "20,671.70", "statement_begin_printed": "20,671.70",
   "statement_end_cents": 2499073, "statement_end": "24,990.73", "statement_end_printed": "24,990.73",
   "difference_cents": 0, "difference": "0.00"}
]}
```

### GET reconciliations/{id}

`{"reconciliation": {...}}`, worked out again from the books as they are now.

### POST reconciliations

Records a statement's figures for a period and compares the books with them, as `reconcile run` does. Balances are typed
as the statement prints them; on a card, what is owed. Running the same period again replaces the figures of an open
reconciliation and keeps its id.

```json
{"account_id": 1, "from": "2025-03-01", "to": "2025-03-31", "begin": "20671.70", "end": "24990.73"}
```

The answer is `{"reconciliation": {...}}`. For these README figures it has `"balanced": true` and
`"difference": "0.00"`. Comparing line by line against a statement file is done with `reconcile run --statement` on the
command line.

Refused with 422 when the period ends before it starts or a figure is not an amount, and with 409 when the period
overlaps a finished one.

### POST reconciliations/{id}/finish

No body. Locks a period that ties to the cent: its rows cannot be deleted or edited until it is reopened, though their
categories can still change. The answer is `{"reconciliation": {...}}` with `"status": "finished"`. Refused with 409
when it is already finished or does not tie, with the same sentence as `reconcile finish`.

### POST reconciliations/{id}/reopen

No body. Unlocks a finished period. Only an account's latest finished period can be reopened, so later periods keep a
sound starting point; anything else is refused with 409.

## A script that books the queue

This reads the queue and books every cafe row to Meals. It uses only Python's standard library.

```python
import json
import urllib.request

BASE = "http://127.0.0.1:8765/api/v1/"
KEY = "paste the key from the address here"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("X-Solo-Books-Token", KEY)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request) as answer:
        return json.load(answer)


waiting = call("GET", "transactions?uncategorized=true&limit=1000")["transactions"]
for row in waiting:
    if "CAFE" in row["merchant"]:
        call("POST", f"transactions/{row['id']}/category", {"category": "Meals"})
```

A refusal raises `urllib.error.HTTPError`; its body holds the `error` object.
