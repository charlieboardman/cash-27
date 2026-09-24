"""Ledger-entry admin API: opening, donation, correction, exchange,
withdrawal (spec 7, 13)."""

from __future__ import annotations

from datetime import date as calendar_date
import uuid

from flask import Blueprint, g, redirect, render_template

from .. import prep, repo
from ..commands import run_command
from ..model import SAFE
from ..money import to_minor
from .app import admin_required, err, json_body, ok

bp = Blueprint("entries", __name__)


@bp.get("/setup")
@admin_required
def opening_page():
    if g.db.execute(
            "SELECT 1 FROM transactions WHERE txn_type='opening'"
            " AND posting_state='active' LIMIT 1").fetchone():
        return redirect("/ledger")
    return render_template("opening.html")


@bp.get("/donation")
@admin_required
def donation_page():
    if not g.db.execute(
            "SELECT 1 FROM transactions WHERE txn_type='opening'"
            " AND posting_state='active' LIMIT 1").fetchone():
        return redirect("/setup")
    state = repo.replay_projected(g.db).states[SAFE]
    return render_template("donation.html", mxn_rate_required=state.mxn <= 0)


@bp.post("/api/entries/<kind>")
@admin_required
def create_entry(kind):
    fns = {"opening": prep.create_opening, "donation": prep.create_donation,
           "correction": prep.create_correction,
           "exchange": prep.create_exchange,
           "withdrawal": prep.create_withdrawal}
    if kind not in fns:
        return err("bad_kind", f"kind must be one of {sorted(fns)}", 400)
    data = json_body("submission_id", "date", "same_day_seq", "usd", "mxn",
                     "assigned_rate_milli", "notes", "document_id", "direction",
                     "related_txn_id", "reason", "source_account")
    usd = to_minor(data.get("usd"), "USD")
    mxn = to_minor(data.get("mxn"), "MXN")
    try:
        sub = str(uuid.UUID(data["submission_id"]))
        date = data["date"]
        calendar_date.fromisoformat(date)
        seq_value = data.get("same_day_seq")
        seq_no = int(seq_value) if seq_value not in (None, "") else None
        rate = data.get("assigned_rate_milli")
        rate = int(rate) if rate not in (None, "", 0) else None
        if rate is not None and rate <= 0:
            raise ValueError("assigned_rate_milli")
        notes = data.get("notes", "")
    except (KeyError, ValueError) as e:
        return err("bad_request", f"Invalid field: {e}")

    doc_id = data.get("document_id")

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
        if doc_id and kind == "exchange":
            prep.complete_exchange_document(
                c, txn_id=out["txn_ids"][0], expected_version=1,
                document_id=doc_id)
        elif doc_id and kind == "withdrawal":
            prep.complete_withdrawal_document(
                c, txn_id=out["txn_ids"][0], expected_version=1,
                document_id=doc_id)
        return out

    result = run_command(g.db, sub, f"entry_{kind}", command)
    return ok(result)
