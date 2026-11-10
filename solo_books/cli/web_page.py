"""The command that serves the books as a local web app, with the review queue and a JSON API."""
from __future__ import annotations

import webbrowser

from .. import db, web
from ..errors import BooksError
from .common import add_command, port_arg


def cmd_web(args) -> int:
    path = db.resolve_db_path(args.db)
    db.connect(path).close()
    try:
        server = web.ReviewServer(path, args.port, verbose=args.verbose)
    except OSError as exc:
        raise BooksError(f"cannot listen on port {args.port}: {exc.strerror}. Another program may have it. Try --port.") from None
    print(f"Books at {server.dashboard_url}  (Ctrl+C stops it)", flush=True)
    print(f"Review queue at {server.url}")
    print(f"The API is under {server.origin}/api/v1/ with the key in the {web.server.KEY_HEADER} header. See docs/API.md.", flush=True)
    if args.open:
        webbrowser.open(server.dashboard_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


def register(commands) -> None:
    p = add_command(commands, "web", cmd_web, "open the books as a local web page, with the review queue and a JSON API", needs_conn=False)
    p.add_argument("--port", type=port_arg, default=8765, help="port on 127.0.0.1 (default 8765)")
    p.add_argument("--open", action="store_true", help="open the dashboard in your browser")
    p.add_argument("--verbose", action="store_true", help="log each request")
