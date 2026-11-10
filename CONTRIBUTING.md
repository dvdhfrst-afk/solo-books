# Contributing

## The rules of this code

- Python 3.9 syntax and the standard library only. No dependencies, at run time or for the tests.
- Money is integer cents. Never a float.
- A person's mistake raises `BooksError` and gets one plain sentence and exit status 2. A stack trace on bad input is a bug.
- Anything written to a user's file goes through `files.write_atomic`.
- Schema changes are a new entry at the end of `MIGRATIONS` in `db.py`. Never edit an old one. Add a test that upgrades
  `tests/fixtures/books-schema-v1.sql` and checks every old row survives.
- Only invented data: "Business A", "Example Bank", made-up merchants. Never commit a real export, a real name or real
  books, including in a bug report.
- No em or en dashes in any file. Source code writes those characters as `\u` escapes when it has to handle them.

## Running the checks

```bash
python -m unittest discover
```

`./check.sh` does the same on a system with a POSIX shell. The tests need no network and open no windows. They include
the repository's own rules, such as that every command in the README parses.

## Tests that do not depend on this program

Totals must be checked against figures this program did not produce: `tests/fixtures/worked/WORKED.md` was added up by
hand, and `samples/expected-2025.json` comes from the script that invented the sample year. Add to those rather than
copying program output into a test.

## Adding a command

The command line lives in `solo_books/cli/`. Each module there holds one area of commands, such as
`reconciling.py` for `reconcile` and its actions. Its `cmd_` functions run one command each, and its
`register(commands)` function adds their parsers. `solo_books/cli/common.py` holds what the areas share: the argument
types for years, ports and counts, `deliver` for output that can go to a file with `-o`, the business and account
lookups, and the runner that turns a failure into one sentence and an exit status.

A new command in an existing area goes in that area's module and its `register`. A new area takes two steps:

1. Write a module in `solo_books/cli/` with its `cmd_` functions and a `register(commands)` function. Use
   `add_command` for a command and `add_group` for one with actions, such as `rules add`. A handler is called with
   `(args, conn)`, or with `(args)` alone when the command is added with `needs_conn=False`.
2. Add the module to `AREAS` in `solo_books/cli/__init__.py`. That list is the order `--help` shows commands in.

Handlers print and return an exit status. Bookkeeping belongs in the modules they call, not in the command line.

## Adding a web address

The local app lives in `solo_books/web/`. `server.py` checks every request before any handler sees it: the Host
header must be the local address, a page needs the key from the printed address, and everything else needs the token
header the page sends. A write to the API must also not come from another site and must have a JSON body, or raw bytes
for an upload. `routes.py` is the table of addresses. `review_queue.py` is the review page and its JSON, `pages.py`
the other pages (their HTML and scripts are in `data/web/`), and the `api_*.py` modules answer `/api/v1/`, with
`api.py` holding what they share.

The review page's own addresses answer every failure the same way: 403 for the wrong host, key or token, 404 for an
address with no handler, 400 for a bad body or a `BooksError`, and 503 when the books cannot be read. The API uses the
statuses in `docs/API.md`: a `BooksError` is 422 with its sentence, an `ApiError(status, message)` is that status, and
`refusals_as(409)` turns the `BooksError` of a write that the books' state refuses into a 409.

To add an address, write its handler in a module in `solo_books/web/` and mark it with one of the decorators from
`routes.py`:

- `@get(path)` reads from the books. It is called with `conn` and returns a dict, sent as JSON.
- `@post(path)` changes the books. It is called with `conn` and the request's JSON object, and returns a dict.
- `@page(path)` is a page opened from the printed address. It is called with the token and a nonce, and returns HTML.
  Every script and style tag needs the nonce, because the page's content security policy allows nothing else.
- `@api(method, path)` is an API address under `/api/v1/`. A path may hold `{id}`, a whole number from 1 up. It is
  called with `conn` and a `Request`, whose `params` holds the path's numbers; `request.fields(...)` and
  `request.params_named(...)` name the body fields and query parameters it accepts, and refuse any others. It returns a
  dict, or a `(status, dict)` pair such as `(201, ...)` for something new. `body="bytes"` takes raw bytes instead of
  JSON, and `needs_conn=False` is for an address that must answer when the books cannot be opened. Add the endpoint to
  `docs/API.md` and a success and a refusal test to `tests/test_api.py`.

A handler raises `BooksError` for a request it cannot answer. A new module must be imported in
`solo_books/web/__init__.py`, since that is what registers its handlers.

## Adding a tax year

The README's section "A new tax year" has the steps. Take every line from the official form, and record the source and
date in the file.

## Running the checks automatically

This repository ships no hosted automation settings. On a service that takes a GitHub Actions style file, this runs the
suite on the oldest and a recent Python, on Linux and Windows:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        python: ["3.9", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: python -m unittest discover
```

That file has not been run, because this repository has no hosted service attached. `docs/TESTING.md` says what has
been run.
