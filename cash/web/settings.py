"""Small administrator-managed accounting configuration."""

from __future__ import annotations

from datetime import datetime, timezone
import io
import secrets
import uuid

from flask import (Blueprint, current_app, g, render_template, request,
                   send_file)

from .. import db_transfer, sync, voucher_defaults, user_access
from ..commands import ValidationFailure, run_command
from ..reference_data import ReferenceDataError, normalize_code
from .app import admin_required, err, json_body, ok

bp = Blueprint("settings", __name__)
MAX_DATABASE_IMPORT_BYTES = 64 * 1024 * 1024


def _submission(data):
    try:
        return str(uuid.UUID(data["submission_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationFailure("bad_submission", "Valid submission_id required") from exc


def _code(data, field):
    try:
        return normalize_code(data.get(field))
    except ReferenceDataError as exc:
        raise ValidationFailure(
            "bad_code", str(exc), {field: "invalid code"}) from exc


@bp.get("/settings")
@admin_required
def settings_page():
    return render_template("settings.html")


@bp.get("/api/settings")
@admin_required
def settings_data():
    accounts = [dict(row) for row in g.db.execute(
        "SELECT account_id, name, active, created_at FROM accounts"
        " WHERE kind='expense' AND account_id!='UNALLOCATED_EXPENSE'"
        " ORDER BY active DESC, name")]
    classes = [dict(row) for row in g.db.execute(
        "SELECT class_code, name, active, created_at FROM classes"
        " ORDER BY active DESC, name")]
    return ok({"expense_accounts": accounts, "classes": classes,
               "default_approver_name": voucher_defaults.approver_name(current_app.config["DATA_ROOT"]),
               "sync": sync.status(current_app.config["DATA_ROOT"])})


@bp.post("/api/settings/sync-token")
@admin_required
def set_sync_token():
    """Replaces any earlier token: the one given, or a random one."""
    data = json_body("token")
    data_root = current_app.config["DATA_ROOT"]
    sync.set_token(data_root, data.get("token"))
    return ok({"sync": sync.status(data_root)})


@bp.delete("/api/settings/sync-token")
@admin_required
def revoke_sync_token():
    data_root = current_app.config["DATA_ROOT"]
    sync.revoke_token(data_root)
    return ok({"sync": sync.status(data_root)})


@bp.post("/api/settings/voucher-defaults")
@admin_required
def save_voucher_defaults():
    data = json_body("approver_name")
    unknown = set(data) - {"submission_id", "approver_name"}
    if unknown:
        return err("unknown_fields", f"Unknown fields: {sorted(unknown)}", 400,
                   {key: "unknown" for key in unknown})
    name = voucher_defaults.save_default(current_app.config["DATA_ROOT"], data.get("approver_name"))
    return ok({"default_approver_name": name})


@bp.post("/api/settings/user-password")
@admin_required
def save_user_password():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return err('bad_request', 'Enter the new user password.', 400)
    unknown = set(data) - {'password', 'confirmation'}
    if unknown:
        return err('unknown_fields', 'Unexpected password setting fields.', 400)
    password = user_access.validate_password(data.get('password'))
    if data.get('confirmation') != password:
        return err('password_mismatch', 'The passwords do not match.', 422,
                   {'confirmation': 'must match the new password'})
    user_access.save_password(current_app.config['AUTH_ENV_FILE'], password)
    return ok({'saved': True})


@bp.post("/api/settings/database/export")
@admin_required
def export_database():
    exported = db_transfer.export_sql(g.db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return send_file(
        io.BytesIO(exported), mimetype="application/sql", as_attachment=True,
        download_name=f"cash-27-database-{stamp}.sql")


@bp.post("/api/settings/database/import/validate")
@admin_required
def validate_database_import():
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
@admin_required
def activate_database_import():
    data = json_body("import_id", "confirmation", "admin_password")
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
@admin_required
def create_expense_account():
    data = json_body("submission_id", "account_id", "name")
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
@admin_required
def create_class():
    data = json_body("submission_id", "class_code", "name")
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
@admin_required
def toggle(kind, code):
    data = json_body("submission_id")
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
