"""Small administrator-managed accounting configuration."""

from __future__ import annotations

from datetime import datetime, timezone
import io
import re
import secrets
import uuid

from flask import (Blueprint, current_app, g, render_template, request,
                   send_file)

from .. import backup_health, db_transfer
from ..commands import ValidationFailure, run_command
from .app import ok, require_admin

bp = Blueprint("settings", __name__)
CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
MAX_DATABASE_IMPORT_BYTES = 64 * 1024 * 1024


def _submission(data):
    try:
        return str(uuid.UUID(data["submission_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationFailure("bad_submission", "Valid submission_id required") from exc


def _code(data, field):
    value = str(data.get(field, "")).strip().upper().replace(" ", "_")
    if not CODE.fullmatch(value):
        raise ValidationFailure(
            "bad_code", "Use 2-32 uppercase letters, numbers, or underscores",
            {field: "invalid code"})
    return value


@bp.get("/settings")
def settings_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("settings.html")


@bp.get("/api/settings")
def settings_data():
    guard = require_admin()
    if guard:
        return guard
    accounts = [dict(row) for row in g.db.execute(
        "SELECT account_id, name, active, created_at FROM accounts"
        " WHERE kind='expense' AND account_id!='UNALLOCATED_EXPENSE'"
        " ORDER BY active DESC, name")]
    classes = [dict(row) for row in g.db.execute(
        "SELECT class_code, name, active, created_at FROM classes"
        " ORDER BY active DESC, name")]
    backups = backup_health.get_backup_health(
        current_app.config["DATA_ROOT"],
        expected=current_app.config["BACKUP_EXPECTED"])
    return ok({"expense_accounts": accounts, "classes": classes,
               "backups": backups})


@bp.post("/api/settings/backups/automatic")
def set_automatic_backups():
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        raise ValidationFailure(
            "enabled_required", "Choose whether automatic backups are on")
    if not enabled and data.get("confirmation") != \
            "TURN OFF AUTOMATIC BACKUPS":
        raise ValidationFailure(
            "confirmation_required",
            "Confirm that you want to turn off automatic backups")
    backup_health.set_automatic_backups(
        current_app.config["DATA_ROOT"], enabled=enabled)
    return ok(backup_health.get_backup_health(
        current_app.config["DATA_ROOT"],
        expected=current_app.config["BACKUP_EXPECTED"]))


@bp.post("/api/settings/database/export")
def export_database():
    guard = require_admin()
    if guard:
        return guard
    exported = db_transfer.export_sql(g.db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return send_file(
        io.BytesIO(exported), mimetype="application/sql", as_attachment=True,
        download_name=f"cash-27-database-{stamp}.sql")


@bp.post("/api/settings/database/import/validate")
def validate_database_import():
    guard = require_admin()
    if guard:
        return guard
    if request.content_length and \
            request.content_length > MAX_DATABASE_IMPORT_BYTES:
        raise ValidationFailure(
            "database_export_too_large", "Database export is larger than 64 MB")
    upload = request.files.get("database_export")
    if not upload or not upload.filename:
        raise ValidationFailure(
            "database_export_required", "Choose a database export file")
    contents = upload.stream.read(MAX_DATABASE_IMPORT_BYTES + 1)
    if len(contents) > MAX_DATABASE_IMPORT_BYTES:
        raise ValidationFailure(
            "database_export_too_large", "Database export is larger than 64 MB")
    try:
        result = db_transfer.stage_import(
            current_app.config["DATA_ROOT"], contents)
    except db_transfer.DatabaseTransferError as exc:
        raise ValidationFailure(
            "invalid_database_export", str(exc)) from exc
    return ok(result)


@bp.post("/api/settings/database/import/activate")
def activate_database_import():
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    if data.get("confirmation") != "REPLACE DATABASE":
        raise ValidationFailure(
            "confirmation_required", "Type REPLACE DATABASE to continue")
    supplied_password = str(data.get("admin_password", ""))
    if not secrets.compare_digest(
            supplied_password, current_app.config["ADMIN_PASSWORD"]):
        raise ValidationFailure(
            "password_required", "Administrator password is incorrect")
    try:
        result = db_transfer.activate_import(
            current_app.config["DATA_ROOT"], g.db,
            str(data.get("import_id", "")))
    except db_transfer.DatabaseTransferError as exc:
        raise ValidationFailure(
            "database_import_failed", str(exc)) from exc
    return ok(result)


@bp.post("/api/settings/expense-accounts")
def create_expense_account():
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    account_id = _code(data, "account_id")
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValidationFailure("name_required", "Account name is required")

    def command(connection):
        if connection.execute(
                "SELECT 1 FROM accounts WHERE account_id=?",
                (account_id,)).fetchone():
            raise ValidationFailure(
                "code_exists", f"Account code {account_id} already exists",
                {"account_id": "already in use"})
        connection.execute(
            "INSERT INTO accounts (account_id, name, kind)"
            " VALUES (?, ?, 'expense')", (account_id, name))
        return {"account_id": account_id, "name": name, "active": True}

    return ok(run_command(g.db, submission, "create_expense_account", command))


@bp.post("/api/settings/classes")
def create_class():
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    class_code = _code(data, "class_code")
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValidationFailure("name_required", "Class name is required")

    def command(connection):
        if connection.execute(
                "SELECT 1 FROM classes WHERE class_code=?",
                (class_code,)).fetchone():
            raise ValidationFailure(
                "code_exists", f"Class code {class_code} already exists",
                {"class_code": "already in use"})
        connection.execute(
            "INSERT INTO classes (class_code, name) VALUES (?, ?)",
            (class_code, name))
        return {"class_code": class_code, "name": name, "active": True}

    return ok(run_command(g.db, submission, "create_class", command))


@bp.post("/api/settings/<kind>/<code>/toggle")
def toggle(kind, code):
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    submission = _submission(data)
    code = _code({"code": code}, "code")
    if kind not in ("class", "expense-account"):
        raise ValidationFailure("bad_kind", "Unknown setting kind")

    def command(connection):
        if kind == "class":
            row = connection.execute(
                "SELECT active FROM classes WHERE class_code=?",
                (code,)).fetchone()
        else:
            row = connection.execute(
                "SELECT active FROM accounts WHERE account_id=?"
                " AND kind='expense' AND account_id!='UNALLOCATED_EXPENSE'",
                (code,)).fetchone()
        if not row:
            raise ValidationFailure("not_found", "Setting not found")
        active = 0 if row["active"] else 1
        if kind == "class":
            connection.execute(
                "UPDATE classes SET active=? WHERE class_code=?",
                (active, code))
        else:
            connection.execute(
                "UPDATE accounts SET active=? WHERE account_id=?",
                (active, code))
        return {"code": code, "active": bool(active)}

    return ok(run_command(g.db, submission, f"toggle_{kind}", command))
