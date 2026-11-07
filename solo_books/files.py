"""Writing files so that a crash, a full disk or a typo cannot leave a half-written or wrong one."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .errors import BooksError

SQLITE_MAGIC = b"SQLite format 3\x00"


def looks_like_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def write_atomic(path: Path, data: bytes) -> None:
    """Replace a file in one step. It holds the old contents or all the new ones, never part of them."""
    path = Path(path)
    if path.is_dir():
        raise BooksError(f"{path} is a folder. Give a file name.")
    if looks_like_sqlite(path):
        raise BooksError(f"{path} looks like a books file, and writing here would destroy it. Choose another name.")
    parent = path.parent if str(path.parent) else Path(".")
    if not parent.is_dir():
        raise BooksError(f"cannot write {path}: the folder {parent} does not exist.")
    temporary = None
    try:
        handle, temporary = tempfile.mkstemp(dir=str(parent), prefix=f".{path.name}.", suffix=".partial")
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if path.exists():
            shutil.copymode(str(path), temporary)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
        raise BooksError(f"cannot write {path}: {exc.strerror or exc}. Nothing was changed.") from None
