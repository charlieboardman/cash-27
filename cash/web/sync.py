"""Read-only endpoints for the offsite sync script (gas/).

The script sends Authorization: Bearer <token>; Settings creates the token.
Database downloads also accept a logged-in administrator, for the Settings
button. Nothing here changes the ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone
import functools
import hashlib
import io
import re

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


def _signed(content, signature, mimetype, suffix):
    """X-Database-Signature is equal for the .db and .sql of the same
    contents, so the script can skip storing an unchanged database."""
    response = send_file(io.BytesIO(content), mimetype=mimetype, as_attachment=True,
                         download_name=f"cash-27-{_stamp()}.{suffix}")
    response.headers["X-Database-Signature"] = signature
    return response


@bp.get("/api/sync/database.db")
@sync_access()
def database_file():
    content, signature = db_transfer.export_database(
        g.db, current_app.config["DATA_ROOT"] / "staging")
    return _signed(content, signature, "application/vnd.sqlite3", "db")


@bp.get("/api/sync/database.sql")
@sync_access()
def database_sql():
    content, signature = db_transfer.export_sql_signed(g.db)
    return _signed(content, signature, "application/sql", "sql")


@bp.get("/api/sync/manifest")
@sync_access(admin_session=False)
def manifest():
    return ok(sync.manifest(g.db, current_app.config["DATA_ROOT"],
                            fy_start_month=current_app.config["FY_START_MONTH"],
                            zone=current_app.config["TIMEZONE"]))


@bp.get("/api/sync/files/<sha256>")
@sync_access(admin_session=False)
def manifest_file(sha256):
    """Only files the manifest lists, and only when they still match."""
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return err("not_found", "No listed file has that hash", 404)
    found = sync.manifest_file(g.db, current_app.config["DATA_ROOT"], sha256)
    if not found:
        return err("not_found", "No listed file has that hash", 404)
    entry, content = found
    if hashlib.sha256(content).hexdigest() != sha256:
        return err("hash_mismatch", f"{entry['path']} no longer matches its recorded hash", 409)
    return send_file(io.BytesIO(content), as_attachment=True, download_name=entry["name"],
                     mimetype="application/pdf" if entry["name"].endswith(".pdf")
                     else "application/json")


@bp.post("/api/sync/complete")
@sync_access(admin_session=False)
def complete():
    """The script's last step. Settings warns when this goes quiet."""
    json_body()
    return ok({"completed_at": sync.record_completion(current_app.config["DATA_ROOT"])})
