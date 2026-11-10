"""The local web app, its JSON API, and the review page. It listens on 127.0.0.1 only.

server.py checks who is asking and hands each request to the handler that routes.py has for its method and path.
Each module that serves pages or JSON registers its handlers when it is imported, so each is imported here.
"""
from . import api_imports, api_overview, api_reconcile, api_rules, api_transactions, pages, review_queue  # noqa: F401
from .server import ReviewServer

__all__ = ["ReviewServer"]
