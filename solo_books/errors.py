"""The one error type the command line turns into a plain message instead of a traceback."""
from __future__ import annotations


class BooksError(Exception):
    """Something the person running the command can understand and fix."""
