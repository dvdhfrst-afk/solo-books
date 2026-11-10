"""The command line. Each command is a thin layer over the module that does the work.

Commands are grouped into areas, one module each. An area has a register(commands) function that adds its
commands to the parser. AREAS lists them in the order --help shows them.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .. import __version__
from . import (
    budgeting,
    businesses,
    categorization_rules,
    exporting,
    history_log,
    importing,
    payee_directory,
    reconciling,
    reporting,
    review_queue,
    setup_safety,
    tagging,
    transactions,
    web_page,
)
from .common import DEBUG_ENV_VAR, PrintableOnly, attach_negative_amounts, db_option, finish_output, guarded, program_name

AREAS = (
    setup_safety,
    businesses,
    importing,
    categorization_rules,
    review_queue,
    transactions,
    tagging,
    payee_directory,
    reconciling,
    reporting,
    budgeting,
    exporting,
    history_log,
    web_page,
)

_EPILOG = """Exit status: 0 done; 1 a finding (a reconciliation that does not tie, a check with problems);
2 a problem with the request or a file, explained on screen; 3 a bug in this program; 130 stopped with Ctrl+C.

This is a bookkeeping tool. Its reports are bookkeeping summaries, not tax or accounting advice."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=program_name(),
        description="Local bookkeeping for one person who owns one or more single-member LLCs.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[db_option()],
    )
    parser.add_argument("--version", action="version", version=f"solo-books {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)
    for area in AREAS:
        area.register(commands)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            # Sent to a file or a pipe, text stays whole whatever the system's old code page can show.
            piped = not stream.isatty() and not os.environ.get("PYTHONIOENCODING")
            stream.reconfigure(encoding="utf-8" if piped else None, errors="replace")
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = PrintableOnly(real_out), PrintableOnly(real_err)
    try:
        parser = build_parser()
        args = parser.parse_args(attach_negative_amounts(sys.argv[1:] if argv is None else list(argv)))
        if not hasattr(args, "db"):
            args.db = None
        return finish_output(guarded(args))
    finally:
        sys.stdout, sys.stderr = real_out, real_err


__all__ = ["DEBUG_ENV_VAR", "build_parser", "main"]
