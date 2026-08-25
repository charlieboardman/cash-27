"""Command executor (spec 14): BEGIN IMMEDIATE, idempotency via
submissions, optimistic versioning, full projected replay validation,
canonical response captured, all-or-nothing commit."""

from __future__ import annotations

import json
import sqlite3

from .repo import ConflictError, replay_projected


class ValidationFailure(Exception):
    def __init__(self, code: str, message: str, fields: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = fields or {}


def run_command(conn: sqlite3.Connection, submission_id: str,
                command_kind: str, fn) -> dict:
    """Run `fn(conn)` as one command. On success records the canonical
    response; a retry of the same submission_id returns it unchanged.

    `fn` raises ValidationFailure for domain errors (rolled back, not
    recorded) and returns a JSON-serializable response dict.
    """
    existing = conn.execute(
        "SELECT state, response_json FROM submissions "
        "WHERE submission_id = ?", (submission_id,)).fetchone()
    if existing:
        if existing["state"] == "committed":
            return json.loads(existing["response_json"])
        # A crashed command left this in_progress with rolled-back
        # effects; it is safe to retry in place.
        conn.execute("DELETE FROM submissions WHERE submission_id = ?",
                     (submission_id,))

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO submissions (submission_id, command_kind) "
            "VALUES (?, ?)", (submission_id, command_kind))
        response = fn(conn)
        replay_projected(conn)  # strict validation of the projected
        # history this command leaves behind
        canonical = json.dumps(response, sort_keys=True)
        conn.execute(
            "UPDATE submissions SET state='committed', response_json=?,"
            " committed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
            " WHERE submission_id=?", (canonical, submission_id))
        conn.execute("COMMIT")
        return response
    except Exception:
        conn.execute("ROLLBACK")
        raise
