"""The pages of the local app, and the navigation every page shares, the review page included.

Each page is data/web/shell.html filled with the shared style and script, the page's own part and its own script.
Everything is served inline under the page's nonce, so the page needs nothing from anywhere else.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

from .routes import page

WEB_DIR = Path(__file__).resolve().parent.parent / "data" / "web"

# (address, title, file name). The review page keeps the address it has always had.
APP_PAGES = (
    ("/dashboard", "Dashboard", "dashboard"),
    ("/transactions", "Transactions", "transactions"),
    ("/accounts", "Accounts", "accounts"),
    ("/imports", "Imports", "imports"),
    ("/rules", "Rules", "rules"),
    ("/reconcile", "Reconcile", "reconcile"),
    ("/payees", "Payees", "payees"),
    ("/budgets", "Budgets", "budgets"),
    ("/reports", "Reports", "reports"),
    ("/history", "History", "history"),
)
NAVIGATION = tuple((path, title) for path, title, _ in APP_PAGES) + (("/", "Review"),)
_PLACEHOLDER = re.compile(r"__([A-Z]+)__")


def nav_links(token: str, current: str) -> str:
    """Links to every page. Each carries the key, since a page opens only with it."""
    links = []
    for path, title in NAVIGATION:
        marker = ' aria-current="page"' if path == current else ""
        links.append(f'<a href="{path}?key={html.escape(token)}"{marker}>{title}</a>')
    return '<nav class="pages" aria-label="Pages">' + "".join(links) + "</nav>"


def _read(name: str) -> str:
    return (WEB_DIR / name).read_text(encoding="utf-8")


def render_page(path: str, title: str, name: str, token: str, nonce: str) -> str:
    values = {
        "TITLE": title,
        "NAV": nav_links(token, path),
        "TOKEN": token,
        "NONCE": nonce,
        "STYLE": _read("app.css"),
        "COMMON": _read("app.js"),
        "BODY": _read(f"{name}.html"),
        "SCRIPT": _read(f"{name}.js"),
    }
    # One pass over the shell, so text inside the inserted parts is never read as a placeholder.
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), _read("shell.html"))


def _register(path: str, title: str, name: str) -> None:
    @page(path)
    def answer(token: str, nonce: str) -> str:
        return render_page(path, title, name, token, nonce)


for _path, _title, _name in APP_PAGES:
    _register(_path, _title, _name)
