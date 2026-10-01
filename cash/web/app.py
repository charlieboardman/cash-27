"""Flask app factory (spec 16-17): shared admin and employee passwords,
signed sessions, CSRF on every mutation, JSON envelope {ok, data} /
{ok:false, error{code,message,fields}}. Money crosses the wire as
decimal strings; unknown request fields are rejected at the schema
layer."""

from __future__ import annotations

import functools
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, g, jsonify, redirect, request, session

from ..commands import ValidationFailure
from ..db import apply_migrations, connect
from ..model import amount_sign_errors
from .. import repo
from ..repo import ConflictError, load_rows

DATA_LAYOUT = ("documents", "staging", "reports", "backups")


def create_app(data_root: str | Path, *, admin_password: str,
               user_password: str | None = None,
               secret_key: str | None = None,
               fy_start_month: int = 9,
               timezone_name: str = "America/Los_Angeles",
               testing: bool = False, auth_env_file: str | Path | None = None) -> Flask:
    # Resolve once against the launch directory. Flask's send_file resolves
    # relative paths against the application package, which otherwise makes a
    # development root such as "data-dev" writable but impossible to stream.
    data_root = Path(data_root).expanduser().resolve()
    for d in DATA_LAYOUT:
        (data_root / d).mkdir(parents=True, exist_ok=True)

    pkg = Path(__file__).resolve().parent.parent
    asset_version = str(sum(p.stat().st_mtime_ns for p in
                            (pkg / "static").rglob("*")
                            if p.is_file()))
    app = Flask(__name__,
                template_folder=str(pkg / "templates"),
                static_folder=str(pkg / "static"),
                static_url_path="/static")
    app.jinja_env.globals["asset_version"] = asset_version
    app.secret_key = secret_key or secrets.token_hex(32)
    app.config.update(DATA_ROOT=data_root, ADMIN_PASSWORD=admin_password,
                      AUTH_ENV_FILE=Path(auth_env_file).expanduser().resolve() if auth_env_file else None,
                      USER_PASSWORD=user_password or admin_password,
                      FY_START_MONTH=fy_start_month,
                      TIMEZONE=ZoneInfo(timezone_name),
                      SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=not testing,
                      PERMANENT_SESSION_LIFETIME=timedelta(days=90),
                      SESSION_REFRESH_EACH_REQUEST=True)

    from .qr_capture import configure
    configure(app)

    conn = connect(data_root / "cash.db")
    apply_migrations(conn, Path(__file__).resolve().parent.parent.parent
                     / "migrations", allow_newer=True)
    invalid_signs = {
        row.row_id: errors for row in load_rows(conn)
        if not row.void and
        (errors := amount_sign_errors(row.txn_type, row.usd, row.mxn))
    }
    if invalid_signs:
        app.logger.error(
            "Ledger amount-sign errors in active transaction IDs: %s. "
            "These rows prevent valid replay; verify the supporting records "
            "and correct the ledger with an audit reason. "
            "No amounts were changed by this startup check.", invalid_signs)
    from .documents import recover_documents
    findings = recover_documents(data_root, conn)
    if findings:
        app.logger.warning("document recovery: %s", findings)
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
                and request.endpoint not in (
                    "auth.login", "auth.user_login", "health", "sync.complete"):
            token = request.headers.get("X-CSRF") or \
                (request.form.get("csrf") if request.form else None)
            if not token or token != session.get("csrf"):
                return err("csrf", "CSRF token missing or invalid", 403)

    from flask import render_template

    @app.context_processor
    def sync_banner():
        from .. import sync
        return {"sync_stale": bool(session.get("admin"))
                and sync.status(data_root)["stale"]}

    @app.get("/")
    def index():
        if session.get("admin"):
            if not repo.has_opening(g.db):
                return redirect("/setup")
            return redirect("/review")
        return redirect("/login")

    @app.get("/login")
    def login_page():
        if session.get("admin"):
            from flask import redirect
            if not repo.has_opening(g.db):
                return redirect("/setup")
            return redirect("/transactions/new")
        return render_template("login.html")

    @app.get("/api/health")
    def health():
        return ok({"status": "up"})

    from . import (auth, balances, capture, entries, export, ledger,
               reports, review, settings, signee, sync, vouchers, bookkeeper)
    app.register_blueprint(auth.bp)
    app.register_blueprint(balances.bp)
    app.register_blueprint(capture.bp)
    app.register_blueprint(entries.bp)
    app.register_blueprint(export.bp)
    app.register_blueprint(ledger.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(bookkeeper.bp)
    app.register_blueprint(review.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(signee.bp)
    app.register_blueprint(sync.bp)
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
        from flask import redirect
        if request.path.startswith("/api/"):
            return err("unauthorized", "Login required", 401)
        return redirect("/login")
    return None


def admin_required(view):
    """Pages redirect to the login page; API calls answer 401."""
    @functools.wraps(view)
    def guarded(*args, **kwargs):
        return require_admin() or view(*args, **kwargs)
    return guarded


def json_body(*allowed) -> dict:
    """The request's JSON object, refusing fields the endpoint does not use
    (spec 14) so a misspelled field fails loudly instead of being ignored.
    A submission_id is always accepted: it is every command's idempotency key."""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        raise ValidationFailure("bad_request", "Send a JSON object")
    unknown = sorted(set(data) - set(allowed) - {"submission_id"})
    if unknown:
        raise ValidationFailure("unknown_fields", f"Unknown fields: {unknown}",
                                {field: "unknown" for field in unknown})
    return data


def client_today() -> str | None:
    """The date on the user's device (X-Client-Date), for dates the server
    records itself, such as when a receipt was reviewed. A device date is at
    most a day from UTC; anything else is ignored and the caller falls back."""
    value = request.headers.get("X-Client-Date", "")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    if day.isoformat() != value or \
            abs((day - datetime.now(timezone.utc).date()).days) > 1:
        return None
    return value
