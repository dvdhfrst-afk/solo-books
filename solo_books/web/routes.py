"""The addresses the local server answers, and the helpers their handlers share.

A handler registers itself with one of four decorators:

- page(path): a page opened from an address the 'web' command prints, with the key in its query string.
  It is called as handler(token, nonce) and returns the HTML text.
- get(path): JSON the review page reads. It is called as handler(conn) and returns a dict.
- post(path): JSON the review page sends. It is called as handler(conn, body), where body is the request's
  JSON object, and returns a dict. These two answer a failure with {"error": "text"}.
- api(method, path): the documented API under /api/v1/ (see docs/API.md). A path can hold {name} parts that
  match a whole number, such as /api/v1/transactions/{id}. The handler is called as handler(conn, request),
  or handler(request) when the route says needs_conn=False, and returns a dict or a (status, dict) pair.
  It answers a failure with {"error": {"code": ..., "message": ...}}.

A handler raises BooksError for a request it cannot answer.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Pattern, Tuple

API_PREFIX = "/api/v1/"
# Ids are whole numbers from 1 up. Eighteen digits keeps every one inside what SQLite can hold.
_ID_PART = "[1-9][0-9]{0,17}"


class Route(NamedTuple):
    handler: Callable
    # "page", "json" (the review page's own JSON) or "api".
    kind: str
    needs_conn: bool = True
    # What an API write takes as its body: a JSON object, or the raw bytes of an uploaded file.
    body: str = "json"


_EXACT: Dict[Tuple[str, str], Route] = {}
_PATTERNS: List[Tuple[str, Pattern[str], Route]] = []


def _compile(path: str) -> Pattern[str]:
    parts = re.split(r"\{(\w+)\}", path)
    regex = "".join(re.escape(part) if index % 2 == 0 else f"(?P<{part}>{_ID_PART})" for index, part in enumerate(parts))
    return re.compile(f"^{regex}$")


def _register(method: str, path: str, route: Route) -> Callable[[Callable], Callable]:
    def decorate(handler: Callable) -> Callable:
        filled = route._replace(handler=handler)
        if "{" in path:
            pattern = _compile(path)
            if any(m == method and p.pattern == pattern.pattern for m, p, _ in _PATTERNS):
                raise RuntimeError(f"{method} {path} has two handlers")
            _PATTERNS.append((method, pattern, filled))
        else:
            if (method, path) in _EXACT:
                raise RuntimeError(f"{method} {path} has two handlers")
            _EXACT[(method, path)] = filled
        return handler

    return decorate


def page(path: str) -> Callable[[Callable], Callable]:
    return _register("GET", path, Route(None, "page"))


def get(path: str) -> Callable[[Callable], Callable]:
    return _register("GET", path, Route(None, "json"))


def post(path: str) -> Callable[[Callable], Callable]:
    return _register("POST", path, Route(None, "json"))


def api(method: str, path: str, needs_conn: bool = True, body: str = "json") -> Callable[[Callable], Callable]:
    return _register(method, API_PREFIX + path, Route(None, "api", needs_conn, body))


def find(method: str, path: str) -> Optional[Tuple[Route, Dict[str, int]]]:
    """The route for this method and path, with the numbers its {name} parts matched."""
    route = _EXACT.get((method, path))
    if route is not None:
        return route, {}
    for wanted, pattern, route in _PATTERNS:
        match = pattern.match(path)
        if wanted == method and match:
            return route, {name: int(value) for name, value in match.groupdict().items()}
    return None


def methods_for(path: str) -> List[str]:
    """Every method some route answers on this path, for a 405 answer's Allow header."""
    found = {method for method, known in _EXACT if known == path}
    found.update(method for method, pattern, _ in _PATTERNS if pattern.match(path))
    return sorted(found)


def to_json(data: Any) -> bytes:
    return json.dumps(data).encode("utf-8")


def from_json(raw: bytes) -> dict:
    """The request body as a JSON object. Raises ValueError, whose text says what was wrong."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except RecursionError:
        raise ValueError("nested too deeply") from None
    if not isinstance(body, dict):
        raise ValueError("expected an object")
    return body


def is_id_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
