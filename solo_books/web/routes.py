"""The addresses the local server answers, and the helpers their handlers share.

A handler registers itself with one of three decorators:

- page(path): a page opened from the address the 'web' command prints. It is called as handler(token, nonce)
  and returns the HTML text.
- get(path): JSON read from the books. It is called as handler(conn) and returns a dict.
- post(path): JSON that changes the books. It is called as handler(conn, body), where body is the request's
  JSON object, and returns a dict.

A handler raises BooksError for a request it cannot answer. The server turns that into a 400 answer.
"""
from __future__ import annotations

import json
from typing import Callable, Dict, NamedTuple, Optional, Tuple


class Route(NamedTuple):
    handler: Callable
    # A page is opened from the printed address, so its key arrives in the query string, not in a header.
    page: bool


_ROUTES: Dict[Tuple[str, str], Route] = {}


def _register(method: str, path: str, page: bool) -> Callable[[Callable], Callable]:
    def decorate(handler: Callable) -> Callable:
        if (method, path) in _ROUTES:
            raise RuntimeError(f"{method} {path} has two handlers")
        _ROUTES[(method, path)] = Route(handler, page)
        return handler

    return decorate


def page(path: str) -> Callable[[Callable], Callable]:
    return _register("GET", path, page=True)


def get(path: str) -> Callable[[Callable], Callable]:
    return _register("GET", path, page=False)


def post(path: str) -> Callable[[Callable], Callable]:
    return _register("POST", path, page=False)


def find(method: str, path: str) -> Optional[Route]:
    return _ROUTES.get((method, path))


def to_json(data: dict) -> bytes:
    return json.dumps(data).encode("utf-8")


def from_json(raw: bytes) -> dict:
    """The request body as a JSON object. Raises ValueError, whose text says what was wrong."""
    body = json.loads(raw.decode("utf-8"))
    if not isinstance(body, dict):
        raise ValueError("expected an object")
    return body


def is_id_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
