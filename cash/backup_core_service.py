"""Private Compose-only service that creates Cash 27 backup archives."""

from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import signal

from .ops import backup_archive, verify_archive


BACKUP_NAME = re.compile(
    r"^cash-27-full-[0-9]{8}T[0-9]{6}(?:[0-9]{6})?Z\.tar\.gz$")
MAX_REQUEST = 4096


class BackupHandler(BaseHTTPRequestHandler):
    server_version = "Cash27BackupCore/1"

    def log_message(self, format, *args):  # noqa: A002
        print(f"backup-core: {format % args}", flush=True)

    def _json(self, status: int, payload: dict) -> None:
        contents = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(contents)))
        self.end_headers()
        self.wfile.write(contents)

    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            self._json(404, {"ok": False})
            return
        self._json(200, {"ok": True})

    def do_POST(self):  # noqa: N802
        if self.path != "/archive":
            self._json(404, {"ok": False})
            return
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        if not hmac.compare_digest(supplied, expected):
            self._json(403, {"ok": False, "error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST:
            self._json(400, {"ok": False, "error": "invalid request"})
            return
        try:
            request = json.loads(self.rfile.read(length))
            name = str(request["name"])
            destination = str(request["destination"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "invalid request"})
            return
        if not BACKUP_NAME.fullmatch(name) or destination not in {
                "outbox", "restore"}:
            self._json(400, {"ok": False, "error": "invalid archive target"})
            return
        target_dir = self.server.state_root / destination
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = target_dir / name
        if target.exists():
            self._json(409, {"ok": False, "error": "archive already exists"})
            return
        try:
            result = backup_archive(self.server.data_root, target)
            verification = verify_archive(target)
        except SystemExit as exc:
            if exc.code == 0:
                raise
            target.unlink(missing_ok=True)
            self._json(500, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:
            target.unlink(missing_ok=True)
            self._json(500, {"ok": False, "error": str(exc)})
            return
        self._json(201, {
            "ok": True,
            "archive": f"/state/{destination}/{name}",
            "sha256": result["sha256"],
            "integrity": verification["integrity"],
        })


def main() -> None:
    token = os.environ.get("CASH_BACKUP_CORE_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("CASH_BACKUP_CORE_TOKEN must contain at least 32 characters")
    server = HTTPServer(("0.0.0.0", 8090), BackupHandler)
    server.token = token
    server.data_root = Path(os.environ.get("CASH_DATA_ROOT", "/data"))
    server.state_root = Path(
        os.environ.get("CASH_BACKUP_STATE_ROOT", "/data/backups/drive"))

    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
