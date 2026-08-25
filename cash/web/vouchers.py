"""Voucher preparation API + print pages (spec 8, 14)."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, g, render_template, request

from .. import prep
from ..commands import run_command
from .app import ok, err, require_admin

bp = Blueprint("vouchers", __name__)


def _cents(data, name):
    """Decimal-string dollars -> integer cents."""
    v = data.get(name, "0") or "0"
    try:
        return int(Decimal(str(v)) * 100)
    except InvalidOperation:
        raise ValueError(name)


@bp.post("/api/vouchers/prepare")
def prepare():
    guard = require_admin()
    if guard:
        return guard
    allowed = {"submission_id", "type", "date", "same_day_seq",
               "recipient", "purpose", "notes", "usd", "mxn"}
    data = request.get_json(force=True, silent=False)
    unknown = set(data) - allowed
    if unknown:
        return err("unknown_fields", f"Unknown fields: {sorted(unknown)}",
                   400, {k: "unknown" for k in unknown})
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        vtype = data["type"]
        date = data["date"]
        seq_no = int(data.get("same_day_seq", 0))
        recipient = data.get("recipient", "")
        purpose = data.get("purpose", "")
        notes = data.get("notes", "")
        usd = _cents(data, "usd")
        mxn = _cents(data, "mxn")
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}")
    if vtype not in ("direct_expense", "signout"):
        return err("bad_type", "direct_expense or signout", 400,
                   {"type": "invalid"})

    fy_month = current_app.config["FY_START_MONTH"]
    fn = (prep.prepare_direct_expense if vtype == "direct_expense"
          else prep.prepare_signout)
    result = run_command(
        g.db, sub, f"prepare_{vtype}",
        lambda c: fn(c, submission_id=sub, date=date, seq_no=seq_no,
                     recipient=recipient, purpose=purpose, notes=notes,
                     usd_cents=usd, mxn_centavos=mxn,
                     fy_start_month=fy_month))
    return ok(result)


@bp.post("/api/addenda/prepare")
def prepare_addendum():
    guard = require_admin()
    if guard:
        return guard
    allowed = {"submission_id", "voucher_id", "kind", "date",
               "same_day_seq", "reason", "usd", "mxn"}
    data = request.get_json(force=True)
    unknown = set(data) - allowed
    if unknown:
        return err("unknown_fields", f"Unknown fields: {sorted(unknown)}",
                   400, {k: "unknown" for k in unknown})
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        usd = _cents(data, "usd")
        mxn = _cents(data, "mxn")
        date = data["date"]
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}")
    result = run_command(
        g.db, sub, f"prepare_addendum_{data['kind']}",
        lambda c: prep.prepare_addendum(
            c, submission_id=sub, voucher_id=data["voucher_id"],
            date=date, seq_no=int(data.get("same_day_seq", 0)),
            kind=data["kind"], reason=data.get("reason", ""),
            usd_cents=usd, mxn_centavos=mxn))
    return ok(result)


@bp.get("/voucher")
def voucher_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("voucher.html")


@bp.get("/voucher/<voucher_id>/print")
def voucher_print(voucher_id):
    """Print page: prefilled from authoritative data; template tokens
    substituted in the browser (spec 8)."""
    guard = require_admin()
    if guard:
        return guard
    v = g.db.execute(
        "SELECT * FROM vouchers WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    if not v:
        return err("not_found", "Voucher not found", 404)
    # The voucher prints only what it authorized. Addenda and accepted
    # claim expenses carry the same voucher_id but are separate
    # documents; summing all active rows would inflate the paper.
    usd = v["authorized_usd_cents"] or 0
    mxn = v["authorized_mxn_centavos"] or 0
    ctx = {
        "VOUCHER_ID": voucher_id,
        "TYPE": v["type"],
        "RECIPIENT": v["recipient"],
        "PURPOSE": v["purpose"],
        "NOTES": v["notes"],
        "USD": f"{usd / 100:.2f}" if usd else "",
        "MXN": f"{mxn / 100:.2f}" if mxn else "",
        "DATE": v["created_at"][:10],
    }
    return render_template("print.html", voucher=dict(v),
                           tokens=ctx)


@bp.get("/voucher/<voucher_id>/addendum/<addendum_id>/print")
def addendum_print(voucher_id, addendum_id):
    guard = require_admin()
    if guard:
        return guard
    a = g.db.execute(
        "SELECT a.*, v.recipient FROM addenda a JOIN vouchers v ON"
        " v.voucher_id = a.voucher_id WHERE a.addendum_id = ?"
        " AND a.voucher_id = ?", (addendum_id, voucher_id)).fetchone()
    if not a:
        return err("not_found", "Addendum not found", 404)
    t = g.db.execute(
        "SELECT usd_cents, mxn_centavos FROM transactions WHERE"
        " addendum_id = ?", (addendum_id,)).fetchone()
    ctx = {
        "ADDENDUM_ID": addendum_id,
        "VOUCHER_ID": voucher_id,
        "DATE": a["created_at"][:10],
        "RECIPIENT": a["recipient"],
        "KIND_LABEL": ("Additional cash authorized"
                       if a["kind"] == "positive" else "Cash returned"),
        "USD": f"{t['usd_cents'] / 100:.2f}" if t and t["usd_cents"] else "",
        "MXN": f"{t['mxn_centavos'] / 100:.2f}" if t and t["mxn_centavos"]
               else "",
    }
    return render_template("addendum.html", tokens=ctx)
