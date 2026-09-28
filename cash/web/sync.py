"""Read-only endpoints for the offsite sync script (gas/).

The script sends Authorization: Bearer <token>; Settings creates the token.
Database downloads also accept a logged-in administrator, for the Settings
button. Nothing here changes the ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import functools
import hashlib
import io
import json
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


def _sheet_value(value):
    return float(value) if isinstance(value, Decimal) else value


def _sheet_tab(report) -> dict:
    """The bookkeeper view as plain rows. Each row repeats its purchase's
    documents so the script can link them to the copies in Drive."""
    columns = [c for c in report["columns"] if c != "Documents"]
    rows, documents = [], []
    for group in report["groups"]:
        linked = [{"reference": d["reference"], "role": d["role"],
                   "sha256": d["sha256"] if d["available"] else None}
                  for d in group["documents"]]
        linked += [{"reference": ref, "role": "", "sha256": None}
                   for ref in group["missing_references"]]
        for row in group["rows"]:
            rows.append([_sheet_value(row["values"].get(c)) for c in columns])
            documents.append(linked)
    return {"name": report["title"], "columns": columns, "rows": rows,
            "documents": documents}


@bp.get("/api/sync/sheets")
@sync_access(admin_session=False)
def sheets():
    """Every fiscal year from the first transaction to the current one, each
    with the bookkeeper Expense report and Cash ledger and a revision that
    changes exactly when that year's content does."""
    from ..prep import fiscal_year_for
    from .bookkeeper import build_report
    fy_month = current_app.config["FY_START_MONTH"]
    today = datetime.now(current_app.config["TIMEZONE"]).date().isoformat()
    current = fiscal_year_for(today, fy_month)
    g.db.execute("BEGIN")
    try:
        first = g.db.execute("SELECT MIN(date) FROM transactions"
                             " WHERE posting_state='active'").fetchone()[0]
        years = range(min(current, fiscal_year_for(first, fy_month)) if first else current,
                      current + 1)
        result = []
        for fy in years:
            tabs = [_sheet_tab(build_report(
                        g.db, current_app.config["DATA_ROOT"], fy_month,
                        {"export_type": kind, "fy": str(fy), "start": "", "end": ""}))
                    for kind in ("expenses", "cash_ledger")]
            revision = hashlib.sha256(json.dumps(tabs, sort_keys=True).encode()).hexdigest()
            result.append({"fiscal_year": fy, "fiscal_label": sync.fiscal_label(fy),
                           "revision": revision, "tabs": tabs})
        ledger_revision = g.db.execute(
            "SELECT ledger_revision FROM ledger_state WHERE singleton_id=1").fetchone()[0]
    finally:
        g.db.rollback()
    return ok({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "ledger_revision": ledger_revision, "years": result})


@bp.post("/api/sync/complete")
@sync_access(admin_session=False)
def complete():
    """The script's last step. Settings warns when this goes quiet."""
    json_body()
    return ok({"completed_at": sync.record_completion(current_app.config["DATA_ROOT"])})
