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
