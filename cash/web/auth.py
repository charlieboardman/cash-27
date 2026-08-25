"""Auth blueprint: one shared admin password, rate-limited login,
signed rolling session, CSRF issuance (spec 16)."""

from __future__ import annotations

import secrets
import time
from collections import defaultdict

from flask import Blueprint, current_app, session

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
    if data.get("password") == current_app.config["ADMIN_PASSWORD"]:
        session.clear()
        session["admin"] = True
        session["csrf"] = secrets.token_hex(24)
        session.permanent = False
        return ok({"csrf": session["csrf"]})
    _attempts[ip].append(time.time())
    return err("bad_credentials", "Wrong password", 401)


@bp.post("/logout")
def logout():
    session.clear()
    return ok({})


@bp.get("/session")
def session_state():
    return ok({"admin": bool(session.get("admin")),
               "csrf": session.get("csrf")})
