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
    txns = g.db.execute(
        "SELECT usd_cents, mxn_centavos FROM transactions WHERE"
        " voucher_id = ? AND posting_state='active'",
        (voucher_id,)).fetchall()
    usd = sum(t["usd_cents"] for t in txns)
    mxn = sum(t["mxn_centavos"] for t in txns)
    ctx = {
        "VOUCHER_ID": voucher_id,
        "TYPE": v["type"],
        "RECIPIENT": v["recipient"],
        "PURPOSE": v["purpose"],
        "NOTES": v["notes"],
        "USD": f"{usd / 100:.2f}" if usd else "",
        "MXN": f"{mxn / 100:.2f}" if mxn else "",
    }
    return render_template("print.html", voucher=dict(v),
                           tokens=ctx)
