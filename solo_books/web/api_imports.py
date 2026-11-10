"""Imports through the API: the record of past files, uploading a new one (or a dry run of it), and undo."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from .. import importer
from ..files import write_atomic
from ..text import clean_cell
from .api import ApiError, Request, account_by_id, money, refusals_as
from .routes import api

# A name for the temporary copy that any system accepts. The books record it as the file's name.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._()\- ]+")
_MAX_NAME = 120


def _import_json(row) -> dict:
    return {
        "id": row["id"],
        "account_id": row["account_id"],
        "account": row["account_name"],
        "file_name": row["file_name"],
        "imported_at": row["imported_at"],
        "rows_read": row["rows_read"],
        "rows_new": row["rows_new"],
        "rows_duplicate": row["rows_duplicate"],
        "rows_skipped": row["rows_skipped"],
        "undone_at": row["undone_at"],
    }


@api("GET", "imports")
def import_list(conn, request: Request) -> dict:
    query = request.params_named("account")
    account_id = query.number("account")
    if account_id is not None:
        account_by_id(conn, account_id)
    return {"imports": [_import_json(row) for row in importer.list_imports(conn, account_id)]}


def _safe_name(given: str) -> str:
    # Only the last part of a path a browser or a script might send, so the copy stays in its own folder.
    name = clean_cell(re.split(r"[\\/]", given)[-1])
    name = _UNSAFE_NAME.sub("_", name).strip(" .")[:_MAX_NAME]
    if not name:
        raise ApiError(400, "filename must name the file, such as export.csv")
    return name


def _result_json(result: importer.ImportResult) -> dict:
    return {
        "dry_run": result.dry_run,
        "import_id": result.import_id,
        "account": result.account,
        "file_name": result.file_name,
        "rows_read": result.rows_read,
        "rows_new": result.rows_new,
        "rows_duplicate": result.rows_duplicate,
        "rows_deleted_before": result.rows_deleted_before,
        "rows_skipped": result.rows_skipped,
        "rows_bad": result.rows_bad,
        "booked_by_rules": result.auto_categorized,
        "waiting_for_review": result.waiting_for_review,
        "earliest": result.earliest.isoformat() if result.earliest else None,
        "latest": result.latest.isoformat() if result.latest else None,
        "samples": [
            dict({"line": row.line, "date": row.posted_on.isoformat(), "description": row.description}, **money("amount", row.amount_cents))
            for row in result.samples
        ],
        "warnings": result.warnings,
        "row_warnings": result.row_warnings,
        "bad_rows": result.bad_rows,
    }


@api("POST", "imports", body="bytes")
def import_upload(conn, request: Request):
    """The body is the export file itself. With dry_run=true nothing is saved, and the answer says what would happen."""
    query = request.params_named("account", "filename", "dry_run", "skip_bad")
    account_id = query.number("account")
    if account_id is None:
        raise ApiError(400, "account is required")
    account = account_by_id(conn, account_id)
    name = _safe_name(query.text("filename") or "")
    if not request.body:
        raise ApiError(400, "the body is empty. Send the export file as the body.")
    dry_run = query.flag("dry_run")
    with tempfile.TemporaryDirectory(prefix="solo-books-upload-") as folder:
        path = Path(folder) / name
        write_atomic(path, request.body)
        result = importer.import_export(conn, account, path, dry_run=dry_run, skip_bad=query.flag("skip_bad"))
    return (200 if dry_run else 201), _result_json(result)


@api("POST", "imports/{id}/undo")
def import_undo(conn, request: Request) -> dict:
    """Take back the rows only this file put in. A finished period, or rows booked by hand without force, refuse."""
    fields = request.fields("force")
    with refusals_as(404):
        importer.get_import(conn, request.params["id"])
    with refusals_as(409):
        outcome = importer.undo_import(conn, request.params["id"], force=fields.flag("force"))
    return dict(outcome, import_id=request.params["id"])
