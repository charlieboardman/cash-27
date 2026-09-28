"""Read-only endpoints for the offsite sync script (gas/).

The script sends Authorization: Bearer <token>; Settings creates the token.
Database downloads also accept a logged-in administrator, for the Settings
button. Nothing here changes the ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone
import functools
import io

from flask import Blueprint, current_app, g, request, send_file, session

from .. import db_transfer, sync
from .app import err, json_body, ok

bp = Blueprint("sync", __name__)


def _bearer_valid() -> bool:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return scheme.lower() == "bearer" and sync.token_valid(
        current_app.config["DATA_ROOT"], token.strip())


def sync_access(*, admin_session=True):
    def decorate(view):
        @functools.wraps(view)
        def guarded(*args, **kwargs):
            if _bearer_valid() or (admin_session and session.get("admin")):
                return view(*args, **kwargs)
            return err("unauthorized", "A valid sync token is required", 401)
        return guarded
    return decorate


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@bp.get("/api/sync/database.db")
@sync_access()
def database_file():
    snapshot = db_transfer.export_database(
        g.db, current_app.config["DATA_ROOT"] / "staging")
    return send_file(io.BytesIO(snapshot), mimetype="application/vnd.sqlite3",
                     as_attachment=True, download_name=f"cash-27-{_stamp()}.db")


@bp.get("/api/sync/database.sql")
@sync_access()
def database_sql():
    return send_file(io.BytesIO(db_transfer.export_sql(g.db)),
                     mimetype="application/sql", as_attachment=True,
                     download_name=f"cash-27-{_stamp()}.sql")


@bp.post("/api/sync/complete")
@sync_access(admin_session=False)
def complete():
    """The script's last step. Settings warns when this goes quiet."""
    json_body()
    return ok({"completed_at": sync.record_completion(current_app.config["DATA_ROOT"])})
