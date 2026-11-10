"""What every command area shares: argument types, output, lookups, and the runner that turns failures into sentences."""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional

from .. import books, db, render
from ..errors import BooksError
from ..periods import FIRST_YEAR, LAST_YEAR, check_year
from ..text import printable

DEBUG_ENV_VAR = "SOLO_BOOKS_DEBUG"

EXIT_OK = 0
# A finding rather than a failure: a reconciliation that does not tie, a check that found problems.
EXIT_FINDING = 1
EXIT_USER_ERROR = 2
EXIT_INTERNAL_ERROR = 3
EXIT_INTERRUPTED = 130


def program_name() -> str:
    """How this program was started, so a hint on screen can be typed back as it stands."""
    started_as = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return "solo-books" if started_as.startswith("solo-books") else "python -m solo_books"


# Building the parser

def db_option() -> argparse.ArgumentParser:
    """The --db flag, which the program and every command accept."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--db", default=argparse.SUPPRESS, help=f"books file (default ${db.DB_ENV_VAR}, else ./{db.DEFAULT_DB_NAME})")
    return parser


def add_command(group, name, handler, help_text, needs_conn=True) -> argparse.ArgumentParser:
    """Add one command. Its handler gets (args, conn), or only (args) when needs_conn is False."""
    sub = group.add_parser(name, help=help_text, description=help_text, parents=[db_option()])
    sub.set_defaults(handler=handler, needs_conn=needs_conn)
    return sub


def add_group(commands, name, help_text):
    """Add a command such as 'rules' whose actions are added to what this returns."""
    sub = commands.add_parser(name, help=help_text, description=help_text, parents=[db_option()])
    return sub.add_subparsers(dest=f"{name}_command", metavar="ACTION", required=True)


def year_arg(text: str) -> int:
    try:
        return check_year(int(text))
    except (ValueError, BooksError):
        raise argparse.ArgumentTypeError(f"not a year from {FIRST_YEAR} to {LAST_YEAR}: {text!r}") from None


def port_arg(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a port number: {text!r}") from None
    if not 0 <= value <= 65535:
        raise argparse.ArgumentTypeError(f"not a port number: {text!r}")
    return value


def positive_arg(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more: {text!r}")
    return value


_NEGATIVE_AMOUNT = re.compile(r"^-\$?[\d,]*\.?\d*$")


def attach_negative_amounts(argv: List[str]) -> List[str]:
    """Let --amount -1,234.50 work. argparse alone takes that value for an unknown flag."""
    result: List[str] = []
    for arg in argv:
        if result and result[-1].startswith("--") and "=" not in result[-1] and _NEGATIVE_AMOUNT.match(arg) and arg != "-":
            result[-1] = f"{result[-1]}={arg}"
        else:
            result.append(arg)
    return result


# Shared by the commands

def deliver(text: str, output: Optional[str]) -> None:
    render.deliver(text, output)
    if output:
        print(f"Wrote {output}")


def scope_ids(conn, args) -> tuple:
    business_id = books.get_business(conn, args.business)["id"] if args.business else None
    account_id = books.get_account(conn, args.account)["id"] if args.account else None
    return business_id, account_id


def chosen_businesses(conn, name: Optional[str]) -> List[sqlite3.Row]:
    if name:
        return [books.get_business(conn, name)]
    rows = books.list_businesses(conn)
    if not rows:
        raise BooksError("no businesses yet. Add one with 'business add NAME'.")
    return rows


# Running a command

class PrintableOnly:
    """Everything shown goes through here, so a hostile row in old books cannot steer the terminal."""

    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        return self._stream.write(printable(text))

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def _run(args) -> int:
    if not args.needs_conn:
        return args.handler(args)
    conn = db.connect(db.resolve_db_path(args.db))
    try:
        return args.handler(args, conn)
    finally:
        conn.close()


def guarded(args) -> int:
    """Turn everything a person can run into into a plain sentence and an exit status."""
    try:
        return _run(args)
    except BooksError as exc:
        print(f"solo-books: {exc}", file=sys.stderr)
        return EXIT_USER_ERROR
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        print("\nsolo-books: stopped. Finished steps are saved, and a step in progress was rolled back.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except sqlite3.Error as exc:
        print(f"solo-books: {db.friendly_error(db.resolve_db_path(args.db), exc)}", file=sys.stderr)
        return EXIT_USER_ERROR
    except OverflowError:
        print("solo-books: a number on the command line or in the file is too large.", file=sys.stderr)
        return EXIT_USER_ERROR
    except OSError as exc:
        where = f"{exc.filename}: " if exc.filename else ""
        print(f"solo-books: {where}{exc.strerror or exc}", file=sys.stderr)
        return EXIT_USER_ERROR
    except MemoryError:
        print("solo-books: this computer ran out of memory. Nothing unfinished was saved.", file=sys.stderr)
        return EXIT_USER_ERROR
    except Exception as exc:  # The last line of defense: a person gets a sentence, not a stack trace.
        if os.environ.get(DEBUG_ENV_VAR):
            raise
        print(
            f"solo-books: this is a bug in the program ({type(exc).__name__}: {exc}). The step in progress was rolled back. "
            f"Set {DEBUG_ENV_VAR}=1 and run it again to see the details.",
            file=sys.stderr,
        )
        return EXIT_INTERNAL_ERROR


def _discard_stdout() -> None:
    """Stop Python's own flush at exit from failing again."""
    try:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        pass


def finish_output(code: int) -> int:
    """A reader such as 'head' may be gone already, which is fine. A full disk under a redirect is not."""
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _discard_stdout()
    except (OSError, ValueError) as exc:
        _discard_stdout()
        if code in (EXIT_OK, EXIT_FINDING):
            print(f"solo-books: the output could not be written: {getattr(exc, 'strerror', None) or exc}.", file=sys.stderr)
            return EXIT_USER_ERROR
    return code
