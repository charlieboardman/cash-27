"""Small administrator-managed accounting configuration."""

from __future__ import annotations

import re
import uuid

from flask import Blueprint, g, render_template, request

from ..commands import ValidationFailure, run_command
from .app import ok, require_admin

bp = Blueprint("settings", __name__)
CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")


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
    return ok({"expense_accounts": accounts, "classes": classes})


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
