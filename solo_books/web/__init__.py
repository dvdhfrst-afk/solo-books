"""A small local web page for working through the review queue. It listens on 127.0.0.1 only.

server.py checks who is asking and hands each request to the handler that routes.py has for its method and path.
Each module that serves pages or JSON registers its handlers when it is imported, so it is imported here.
"""
from . import review_queue  # noqa: F401  (registers its routes)
from .server import ReviewServer

__all__ = ["ReviewServer"]
