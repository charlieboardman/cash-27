"""Ledger-entry admin API: opening, donation, correction, exchange,
withdrawal (spec 7, 13)."""

from __future__ import annotations

from datetime import date as calendar_date
import uuid
from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, request

from .. import prep
from ..commands import run_command
from .app import ok, err, require_admin

bp = Blueprint("entries", __name__)


def _cents(data, name):
    v = data.get(name, "0") or "0"
    try:
        value = Decimal(str(v))
        if value.as_tuple().exponent < -2:
            raise ValueError(name)
        return int(value * 100)
    except (InvalidOperation, ValueError):
        raise ValueError(name)


@bp.post("/api/entries/<kind>")
def create_entry(kind):
    guard = require_admin()
    if guard:
        return guard
    fns = {"opening": prep.create_opening, "donation": prep.create_donation,
           "correction": prep.create_correction,
           "exchange": prep.create_exchange,
           "withdrawal": prep.create_withdrawal}
    if kind not in fns:
        return err("bad_kind", f"kind must be one of {sorted(fns)}", 400)
    data = request.get_json(force=True)
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        date = data["date"]
        calendar_date.fromisoformat(date)
        seq_no = int(data.get("same_day_seq", 0))
        usd = _cents(data, "usd")
        mxn = _cents(data, "mxn")
        rate = data.get("assigned_rate_milli")
        rate = int(rate) if rate not in (None, "", 0) else None
        notes = data.get("notes", "")
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}")

    doc_id = data.get("document_id")
    if doc_id and kind in ("exchange", "withdrawal"):
        d = g.db.execute(
            "SELECT state FROM documents WHERE document_id = ?",
            (doc_id,)).fetchone()
        if not d or d["state"] != "inbox":
            return err("not_in_inbox", "Document must be in inbox", 409,
                       {"document_id": "not inbox"})

    def inner(c):
        if kind == "opening":
            return fns[kind](c, submission_id=sub, date=date,
                             usd_cents=usd, mxn_centavos=mxn,
                             assigned_rate_milli=rate, notes=notes)
        if kind == "donation":
            return fns[kind](c, submission_id=sub, date=date,
                             usd_cents=usd, mxn_centavos=mxn,
                             assigned_rate_milli=rate, notes=notes)
        if kind == "correction":
            return fns[kind](c, submission_id=sub, date=date,
                             seq_no=seq_no, direction=data["direction"],
                             usd_cents=usd, mxn_centavos=mxn,
                             assigned_rate_milli=rate,
                             related_txn_id=data.get("related_txn_id"),
                             reason=data.get("reason", ""), notes=notes)
        if kind == "exchange":
            return fns[kind](c, submission_id=sub, date=date,
                             seq_no=seq_no,
                             source_account=data.get("source_account",
                                                     "SAFE"),
                             usd_cents=usd, mxn_centavos=-abs(mxn),
                             notes=notes)
        return fns[kind](c, submission_id=sub, date=date, seq_no=seq_no,
                         usd_cents=usd, notes=notes)

    def command(c):
        out = inner(c)
        if doc_id and kind in ("exchange", "withdrawal"):
            for txn_id in out["txn_ids"]:
                c.execute(
                    "INSERT INTO transaction_documents (txn_id,"
                    " document_id, role) VALUES (?,?, 'support')",
                    (txn_id, doc_id))
            c.execute(
                "UPDATE documents SET state='filed', doc_type=? WHERE"
                " document_id = ?", (f"{kind}_receipt", doc_id))
        return out

    result = run_command(g.db, sub, f"entry_{kind}", command)
    return ok(result)
