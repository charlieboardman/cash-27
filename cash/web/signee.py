"""Signee page and APIs (spec 12, 15): predictable voucher-scoped URL,
no admin password, anonymous signed session + CSRF for forms only."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation

from pathlib import Path

from flask import (Blueprint, current_app, g, render_template,
                   request, session)

from ..commands import run_command
from .. import packages, repo
from .app import ok, err
from .documents import normalize_document

bp = Blueprint("signee", __name__)
VOUCHER_RE = re.compile(r"^FY\d{2}-\d{4}$")


def _voucher_or_none(voucher_id):
    if not VOUCHER_RE.match(voucher_id):
        return None
    return g.db.execute(
        "SELECT * FROM vouchers WHERE voucher_id = ? AND"
        " type='signout'", (voucher_id,)).fetchone()


def _signee_guard(voucher_id):
    v = _voucher_or_none(voucher_id)
    if not v:
        from flask import request as _req
        if _req.path.endswith("/state") or "/claims" in _req.path                 or "/packages" in _req.path or "/documents" in _req.path:
            return None, err("not_found", "Unknown voucher", 404)
        return None, err("not_found", "Unknown voucher", 404)
    if not session.get("csrf"):
        session["csrf"] = uuid.uuid4().hex  # anonymous signed session
    return v, None


def _cents(v):
    if v in (None, "", 0):
        return 0
    return int(Decimal(str(v)) * 100)


@bp.get("/FY<fy>-<num>")
def signee_page(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    return render_template("signee.html", voucher_id=vid)


@bp.get("/FY<fy>-<num>/state")
def signee_state(fy, num):
    """Projected state: authoritative balance + draft effects (spec 12)."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    res = repo.replay_projected(g.db)
    st = res.states.get(vid)
    claims = g.db.execute(
        "SELECT claim_id, claim_date, vendor, description,"
        " face_value_minor, face_currency, usd_paid_cents,"
        " mxn_paid_centavos, mxn_change_centavos, usd_change_cents,"
        " state, admin_note FROM signout_expense_claims WHERE"
        " voucher_id = ? ORDER BY created_at", (vid,)).fetchall()
    pkgs = g.db.execute(
        "SELECT package_id, seq, status, admin_summary FROM"
        " signout_reconciliation_packages WHERE voucher_id = ?"
        " ORDER BY seq", (vid,)).fetchall()
    drafts_usd = sum(c["usd_paid_cents"] - c["usd_change_cents"]
                     for c in claims if c["state"] == "draft")
    drafts_mxn = sum(c["mxn_paid_centavos"] - c["mxn_change_centavos"]
                     for c in claims if c["state"] == "draft")
    submitted_usd = sum(c["usd_paid_cents"] - c["usd_change_cents"]
                        for c in claims if c["state"] == "submitted")
    submitted_mxn = sum(c["mxn_paid_centavos"] - c["mxn_change_centavos"]
                        for c in claims if c["state"] == "submitted")
    authoritative = None if st is None else {
        "usd": f"{st.usd / 100:.2f}", "mxn": f"{st.mxn / 100:.2f}"}
    proj_usd = (st.usd - drafts_usd - submitted_usd) if st else 0
    proj_mxn = (st.mxn - drafts_mxn - submitted_mxn) if st else 0
    return ok({
        "voucher": {"voucher_id": vid, "purpose": v["purpose"],
                    "recipient": v["recipient"], "status": v["status"]},
        "authoritative": authoritative,
        "projected_after_pending": {
            "usd": f"{proj_usd / 100:.2f}", "mxn": f"{proj_mxn / 100:.2f}"},
        "claims": [dict(c) for c in claims],
        "packages": [dict(p) for p in pkgs],
        "csrf": session.get("csrf"),
    })


@bp.post("/FY<fy>-<num>/documents")
def signee_upload(fy, num):
    """Signee uploads receipt PDFs/images; normalized like capture,
    stored as signee_draft, voucher-scoped."""
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    token = request.headers.get("X-CSRF")
    if not token or token != session.get("csrf"):
        return err("csrf", "CSRF token missing or invalid", 403)
    files = request.files.getlist("files")
    if not files:
        return err("no_files", "No files", 422)
    inbox = current_app.config["DATA_ROOT"] / "inbox"
    created = []
    for f in files:
        document_id = str(uuid.uuid4())
        meta = normalize_document(f.stream, inbox, document_id)
        g.db.execute(
            "INSERT INTO documents (document_id, voucher_id, rel_path,"
            " sha256, size_bytes, page_count, state) VALUES (?,?,?,?,?,?,"
            " 'signee_draft')",
            (document_id, vid,
             str(Path(meta["path"]).relative_to(
                 current_app.config["DATA_ROOT"])), meta["sha256"],
             meta["size"], meta["pages"]))
        created.append({"document_id": document_id, **meta})
    g.db.commit()
    return ok({"documents": created})


@bp.post("/FY<fy>-<num>/claims")
def create_claim_route(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    token = request.headers.get("X-CSRF")
    if not token or token != session.get("csrf"):
        return err("csrf", "CSRF token missing or invalid", 403)
    data = request.get_json(force=True)
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        doc = data["document_id"]
        face = _cents(data.get("face_value"))
        currency = data["face_currency"]
        if currency not in ("USD", "MXN"):
            raise ValueError("face_currency")
        body = dict(
            voucher_id=vid, document_id=doc, claim_date=data["date"],
            vendor=data.get("vendor", ""),
            description=data.get("description", ""),
            face_value_minor=face, face_currency=currency,
            usd_paid_cents=_cents(data.get("usd_paid")),
            mxn_paid_centavos=_cents(data.get("mxn_paid")),
            mxn_change_centavos=_cents(data.get("mxn_change")),
            usd_change_cents=_cents(data.get("usd_change")),
            notes=data.get("notes", ""))
    except (KeyError, ValueError, InvalidOperation) as e:
        return err("bad_request", f"Invalid field: {e}", 400)
    result = run_command(g.db, sub, "create_claim",
                         lambda c: packages.create_claim(c, **body))
    return ok(result)


@bp.post("/FY<fy>-<num>/packages")
def submit_package_route(fy, num):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    token = request.headers.get("X-CSRF")
    if not token or token != session.get("csrf"):
        return err("csrf", "CSRF token missing or invalid", 403)
    data = request.get_json(force=True)
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        claim_ids = data["claim_ids"]
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}", 400)
    result = run_command(
        g.db, sub, "submit_package",
        lambda c: packages.submit_package(c, voucher_id=vid,
                                          claim_ids=claim_ids))
    return ok(result)


@bp.post("/FY<fy>-<num>/packages/<package_id>/withdraw")
def withdraw_route(fy, num, package_id):
    vid = f"FY{fy}-{num}"
    v, failure = _signee_guard(vid)
    if failure:
        return failure
    data = request.get_json(force=True)
    result = run_command(
        g.db, str(uuid.UUID(data["submission_id"])), "withdraw_package",
        lambda c: packages.withdraw_package(c, voucher_id=vid,
                                            package_id=package_id))
    return ok(result)
