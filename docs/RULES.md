# Rules

A rule books bank rows to a category without you looking at each one. It has conditions, all of which must match a
row, and actions: the category it books and, if you give one, a note it puts on the rows. Rules belong to one business
and are tried in order. The first enabled rule whose conditions all match books the row. A row nothing matches waits in
the review queue.

Rules run when a file is imported (unless the `import_runs_rules` setting is off), when a rule is added, edited or
enabled, and with `rules apply`. They never touch a row you booked by hand.

## Conditions

A rule has at least one condition, and at most one from each group. Wording is never case-sensitive, and runs of
spaces count as one.

| Group | Kind | `rules add` flag | Matches when |
|---|---|---|---|
| description | `contains` | `--contains TEXT` | the description, the merchant name or the memo contains the text |
| description | `starts_with` | `--starts-with TEXT` (or `--starts`) | the description starts with the text |
| description | `exact` | `--exact TEXT` | the whole description, or the merchant name, is exactly the text |
| description | `regex` | `--regex PATTERN` | a regular expression finds a match in the description or the memo |
| amount | `amount_equals` | `--amount-min X --amount-max X` | the size of the amount is exactly X |
| amount | `amount_at_least` | `--amount-min X` (or `--min`) | the size is X or more |
| amount | `amount_at_most` | `--amount-max X` (or `--max`) | the size is X or less |
| amount | `amount_between` | `--amount-min X --amount-max Y` | the size is from X to Y, both included |
| direction | `direction` | `--direction in` or `out` | money comes in (a positive amount) or goes out (a negative one) |
| account | `account` | `--account NAME` | the row is in that account |
| date | `date_between` | `--from DAY --to DAY` | the row is dated on or after the first day and on or before the last; either can be left out |
| day | `day_of_month_between` | `--day-from N --day-to N` | the day of the month is from N to N. `--day-from 25 --day-to 5` wraps past the end of the month. Either can be left out |

Amounts are sizes, written without a minus sign: 850.00 matches 850.00 out and 850.00 in. Add `--direction` to tell them
apart. The merchant name is the one `rules test` prints, the same name the review queue groups by.

A regular expression is refused if it does not compile, or if it has a repeat inside a repeat, such as `(a+)+`, which
can take years to fail on one line of text. A pattern can be up to 300 characters.

## Actions

| Kind | `rules add` flag | Does |
|---|---|---|
| `book_category` | `--category CODE` | books the row to that category. Every rule has exactly one. |
| `set_note` | `--note TEXT` | puts the note on each row the rule books |

A rule's note goes on a row that has no note, and replaces a note an earlier rule wrote. A note you wrote with
`txn note`, or gave to `txn add`, is never replaced. When a later `rules apply --recheck` books a row with a rule that has
no note, or sends it back to review, a note a rule wrote is cleared. `txn show` marks a note a rule wrote.

## Adding a rule

```bash
python -m solo_books rules add --business "Business A" --exact "REDWOOD PLAZA RENT" --amount-min 850.00 --amount-max 850.00 --day-from 1 --day-to 10 --note "Suite 4 lease" --category "Office rent"
```

That books rows whose merchant reads exactly `REDWOOD PLAZA RENT`, for exactly 850.00, dated in the first ten days of a
month, to Office rent, with a note. A new rule goes in at priority 100 unless `--priority` says otherwise.

## Order

Rules are tried by priority, lowest number first. Rules with the same priority are tried oldest first. `rules list`
shows the order, each rule's conditions and action, whether it is on, and how many rows it booked.

```bash
python -m solo_books rules list --business "Business A"
```

`rules move` changes where one rule comes, either to a place counted from 1 or just before another rule. Afterward the
business's rules are numbered 1, 2, 3 and so on, in their new order.

```bash
python -m solo_books rules move 3 --to 1
```

```bash
python -m solo_books rules move 3 --before 1
```

Moving, editing or disabling a rule does not rebook rows it or others booked earlier. `rules apply --recheck` runs every
rule again over the rows rules booked, and over the queue.

## Changing, disabling and enabling

```bash
python -m solo_books rules edit 3 --amount-max 900.00 --note "Suite 4 lease, renewed"
```

A condition given to `rules edit` replaces the rule's condition from the same group. `--drop GROUP` removes one, using
the group names in the table above. `--category` changes what it books, `--note` changes its note and `--no-note` takes
the note away. A rule left with no conditions is refused.

```bash
python -m solo_books rules disable 3
```

```bash
python -m solo_books rules enable 3
```

A disabled rule keeps its place and its rows, and books nothing until it is enabled.

## Why a row was or was not booked

`rules explain` takes a transaction id, as `txn list` shows it. It goes through the business's rules in order and says,
for each condition, whether it held and what the row showed it. It names the rule that books the row, and says when the
row is booked by hand, held back from a rule, or booked by a rule that no longer comes first.

```bash
python -m solo_books rules explain 2
```

It exits with status 1 when no enabled rule books the row.

`rules test` does the same for a row that is not in the books yet. Give it the description, and the amount, account and
date when rules look at them. `--why` shows every rule tried.

```bash
python -m solo_books rules test "REDWOOD PLAZA RENT" --business "Business A" --amount -850.00 --account "A Checking" --date 2025-02-03 --why
```

## Rule files

`rules export` writes CSV by default, with the columns rules have always had: `priority`, `match`, `pattern`,
`category`, `direction`, `min`, `max` and `account`. `rules import` reads the same columns. That keeps working for any
rule those columns can carry. A rule with a date or day condition, a note, no wording condition, or that is disabled
cannot be written as CSV, and `rules export` stops and names it rather than leave it out.

A rule pack carries everything. It is JSON:

```bash
python -m solo_books rules export --business "Business A" --format json -o rules-business-a.json
```

```bash
python -m solo_books rules import rules-business-a.json --business "Business A"
```

A file ending in `.json` is read and written as a pack without `--format json`. A pack looks like this:

```json
{
  "pack_version": 1,
  "rules": [
    {
      "priority": 1,
      "enabled": true,
      "conditions": [
        {"kind": "exact", "text": "REDWOOD PLAZA RENT"},
        {"kind": "amount_equals", "amount": "850.00"},
        {"kind": "day_of_month_between", "from": 1, "to": 10}
      ],
      "actions": [
        {"kind": "book_category", "category": "6105"},
        {"kind": "set_note", "note": "Suite 4 lease"}
      ]
    }
  ]
}
```

Each condition kind writes its own keys: `text` for the four wording kinds, `amount` for `amount_equals`,
`amount_at_least` and `amount_at_most`, `min` and `max` for `amount_between`, `direction` (`in` or `out`), `account` (the
account's name), `from` and `to` as YYYY-MM-DD dates for `date_between`, and `from` and `to` as day numbers for
`day_of_month_between`. Amounts are written as text, such as `"850.00"`, so no figure is ever rounded. A category is
named by its code or its name. `priority` and `enabled` can be left out, for 100 and true.

Importing a pack checks every rule first: the keys, the condition and action kinds, the categories and accounts, and
the regular expressions. If one rule is wrong, the message names it and no rule is added. A rule the business already
has, with the same conditions, category and note, is skipped and counted, so importing the same pack twice adds nothing
the second time. A pack from a newer version of the program, with a higher `pack_version`, is refused.

## Rules from earlier versions

Books from before rules had several conditions are upgraded the first time they are opened. Every rule keeps its id,
its priority, its category and what it matched: its wording becomes a wording condition, and its direction, size limits
and account become conditions of their own. The results are the same as before.
