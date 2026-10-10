"""A tiny fake Affinity API for the cowork-harness regression tests.

Serves synthetic data only (fixtures.json): routes are "METHOD /path" or "METHOD /path?query"
(the query form wins when it matches exactly). Writes are refused (a read-only test org).
Unmatched requests get 404 and are appended to $FAKEAFF_UNMATCHED (default
/tmp/fakeaff-unmatched.log) so missing fixtures are easy to find.

Usage: python3 server.py [port]   (default 8765, binds 127.0.0.1)
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

FIXTURES = json.loads((Path(__file__).with_name("fixtures.json")).read_text())
UNMATCHED = os.environ.get("FAKEAFF_UNMATCHED", "/tmp/fakeaff-unmatched.log")
BASE = "http://127.0.0.1:{port}"


def _lookup(method: str, raw_path: str) -> object | None:
    parts = urlsplit(raw_path)
    for key in (f"{method} {parts.path}?{parts.query}", f"{method} {parts.path}"):
        if key in FIXTURES:
            return FIXTURES[key]
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "fakeaff/1"

    def _send(self, status: int, body: object) -> None:
        text = json.dumps(body).replace("{base}", BASE.format(port=self.server.server_port))
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Affinity-Api-Version", "2026-07-15")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        body = _lookup("GET", self.path)
        if body is None:
            with Path(UNMATCHED).open("a") as log:
                log.write(f"GET {self.path}\n")
            self._send(404, {"errors": [{"code": "not-found", "message": "fake: no fixture"}]})
            return
        self._send(200, body)

    def _refuse(self) -> None:
        with Path(UNMATCHED).open("a") as log:
            log.write(f"{self.command} {self.path} (write refused)\n")
        self._send(403, {"errors": [{"code": "permission", "message": "read-only test org"}]})

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

    def log_message(self, *_args: object) -> None:
        return


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
