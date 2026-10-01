"""Rate-limited shared administrator and employee logins."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict

from flask import Blueprint, current_app, g, session

from .. import repo, user_access
from ..commands import ValidationFailure
from .app import ok, err

bp = Blueprint("auth", __name__, url_prefix="/api")

_attempts: dict[str, list[float]] = defaultdict(list)
WINDOW_S, MAX_ATTEMPTS = 300, 10


def _rate_limited(key: str) -> bool:
    now = time.time()
    hits = [t for t in _attempts[key] if now - t < WINDOW_S]
    _attempts[key] = hits
    return len(hits) >= MAX_ATTEMPTS


@bp.post("/login")
def login():
    from flask import request
    ip = request.remote_addr or "local"
    if _rate_limited(ip):
        return err("rate_limited", "Too many attempts", 429)
    data = request.get_json(silent=True) or request.form
    supplied = data.get("password")
    if isinstance(supplied, str) and secrets.compare_digest(
            supplied.encode("utf-8"),
            current_app.config["ADMIN_PASSWORD"].encode("utf-8")):
        session.clear()
        session["admin"] = True
        session["csrf"] = secrets.token_hex(24)
        session.permanent = data.get("remember") in (True, "on")
        return ok({"csrf": session["csrf"],
                   "next_url": "/transactions/new" if repo.has_opening(g.db) else "/setup"})
    _attempts[ip].append(time.time())
    return err("bad_credentials", "Wrong password", 401)


@bp.post("/user-login")
def user_login():
    """One shared password protects all employee receipt workspaces."""
    from flask import request
    ip = request.remote_addr or "local"
    key = f"user:{ip}"
    if _rate_limited(key):
        return err("rate_limited", "Too many attempts", 429)
    data = request.get_json(silent=True) or request.form
    try:
        expected = user_access.password(current_app.config["AUTH_ENV_FILE"],
                                        current_app.config["USER_PASSWORD"])
    except ValidationFailure as exc:
        return err(exc.code, exc.message, 503)
    supplied = data.get("password")
    if isinstance(supplied, str) and secrets.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8')):
        next_url = str(data.get("next") or "")
        if not next_url.startswith("/FY") or "//" in next_url:
            next_url = "/"
        session.clear()
        session["user"] = True
        session["csrf"] = secrets.token_hex(24)
        session.permanent = data.get("remember") in (True, "on")
        return ok({"csrf": session["csrf"], "next_url": next_url})
    _attempts[key].append(time.time())
    return err("bad_credentials", "Wrong password", 401)


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    from flask import redirect, request
    if request.method == "GET":
        return redirect("/login")
    return ok({})


@bp.get("/session")
def session_state():
    return ok({"admin": bool(session.get("admin")),
               "user": bool(session.get("user")),
               "csrf": session.get("csrf")})
