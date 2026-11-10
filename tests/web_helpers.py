"""A real server on an unused port, with the real per-run key, for the web and API tests. No browser is opened."""
from __future__ import annotations

import http.client
import json
import threading
from typing import Optional, Tuple
from urllib.parse import urlencode

from solo_books.web import ReviewServer
from tests.helpers import BooksCase


class ServerCase(BooksCase):
    def setUp(self) -> None:
        super().setUp()
        self.server = ReviewServer(self.db_path, port=0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def raw(self, method: str, path: str, body: Optional[bytes] = None, headers: Optional[dict] = None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(client.close)
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, response.read(), dict(response.getheaders())

    def call(
        self,
        method: str,
        path: str,
        body=None,
        query: Optional[dict] = None,
        headers: Optional[dict] = None,
        key: Optional[str] = None,
        upload: Optional[bytes] = None,
    ) -> Tuple[int, dict, dict]:
        """One request to /api/v1/PATH, sent the way a careful script or the app's own pages send it."""
        sent = {"X-Solo-Books-Token": self.server.token if key is None else key}
        payload = None
        if upload is not None:
            sent["Content-Type"] = "application/octet-stream"
            payload = upload
        elif body is not None:
            sent["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        sent.update(headers or {})
        url = "/api/v1/" + path + ("?" + urlencode(query) if query else "")
        status, data, answer_headers = self.raw(method, url, payload, sent)
        return status, json.loads(data), answer_headers

    def ok_call(self, method: str, path: str, body=None, query=None, upload=None, status: int = 200) -> dict:
        got, data, _ = self.call(method, path, body, query, upload=upload)
        self.assertEqual(got, status, f"{method} {path}: {data}")
        return data

    def refused(self, status: int, method: str, path: str, body=None, query=None, upload=None, **kwargs) -> str:
        """The request fails with this status and the documented error shape; gives back its message."""
        got, data, _ = self.call(method, path, body, query, upload=upload, **kwargs)
        self.assertEqual(got, status, f"{method} {path}: {data}")
        self.assertEqual(set(data), {"error"})
        self.assertEqual(set(data["error"]), {"code", "message"})
        self.assertIsInstance(data["error"]["message"], str)
        return data["error"]["message"]
