"""Flask app factory (spec 16-17): one shared admin password, signed
rolling sessions, CSRF on every mutation, JSON envelope {ok, data} /
{ok:false, error{code,message,fields}}. Money crosses the wire as
decimal strings; unknown request fields are rejected at the schema
layer."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from flask import Flask, g, jsonify, request, session

from ..commands import ValidationFailure
from ..db import apply_migrations, connect
from ..repo import ConflictError

DATA_LAYOUT = ("inbox", "documents", "staging", "reports", "backups")


def create_app(data_root: str | Path, *, admin_password: str,
               secret_key: str | None = None,
               fy_start_month: int = 8,
               testing: bool = False) -> Flask:
    data_root = Path(data_root)
    for d in DATA_LAYOUT:
        (data_root / d).mkdir(parents=True, exist_ok=True)

    pkg = Path(__file__).resolve().parent.parent
    app = Flask(__name__,
                template_folder=str(pkg / "templates"),
                static_folder=str(pkg / "static"),
                static_url_path="/static")
    app.secret_key = secret_key or secrets.token_hex(32)
    app.config.update(DATA_ROOT=data_root, ADMIN_PASSWORD=admin_password,
                      FY_START_MONTH=fy_start_month,
                      SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=not testing)

    conn = connect(data_root / "cash.db")
    apply_migrations(conn, Path(__file__).resolve().parent.parent.parent
                     / "migrations")
    conn.close()

    @app.before_request
    def open_db():
        g.db = connect(data_root / "cash.db")

    @app.teardown_request
    def close_db(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.before_request
    def csrf_guard():
        if request.method in ("POST", "PUT", "PATCH", "DELETE") \
                and request.endpoint not in ("auth.login", "health"):
            token = request.headers.get("X-CSRF") or \
                (request.form.get("csrf") if request.form else None)
            if not token or token != session.get("csrf"):
                return err("csrf", "CSRF token missing or invalid", 403)

    @app.get("/api/health")
    def health():
        return ok({"status": "up"})

    from . import auth, entries, vouchers
    app.register_blueprint(auth.bp)
    app.register_blueprint(entries.bp)
    app.register_blueprint(vouchers.bp)

    @app.errorhandler(ValidationFailure)
    def _vf(e):
        return err(e.code, e.message, 422, e.fields)

    @app.errorhandler(ConflictError)
    def _cf(e):
        return err("conflict", str(e), 409)

    return app


def ok(data, status=200):
    return jsonify({"ok": True, "data": data}), status


def err(code, message, status=400, fields=None):
    return jsonify({"ok": False, "error": {
        "code": code, "message": message,
        "fields": fields or {}}}), status


def require_admin():
    if not session.get("admin"):
        return err("unauthorized", "Login required", 401)
    return None
