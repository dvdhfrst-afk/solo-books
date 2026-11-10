"""The local web app, its JSON API, and the review page. It listens on 127.0.0.1 only.

server.py checks who is asking and hands each request to the handler that routes.py has for its method and path.
Each module that serves pages or JSON registers its handlers when it is imported, so each is imported here.
"""
from . import (  # noqa: F401
    api_budgets,
    api_history,
    api_imports,
    api_overview,
    api_payees,
    api_reconcile,
    api_reports,
    api_rules,
    api_transactions,
    pages,
    review_queue,
)
from .server import ReviewServer

__all__ = ["ReviewServer"]
