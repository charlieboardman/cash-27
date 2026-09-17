"""Current balances page (spec 10): replayed SAFE/signout state,
physical SAFE count comparison, and one-glance conservation totals.
The bank is an external source, not a tracked pool; withdrawals inject
cash and no bank balance is tracked or shown."""

from __future__ import annotations

from datetime import date as calendar_date
from decimal import Decimal, InvalidOperation
import uuid

from flask import Blueprint, g, render_template, request

from .. import prep, repo, signouts
from ..commands import ValidationFailure, run_command
from ..model import SAFE
from .app import err, ok, require_admin

bp = Blueprint("balances", __name__)


def _count_minor(data, name):
    try:
        value = Decimal(str(data.get(name, "0") or "0"))
        if not value.is_finite() or value < 0 or \
                value.as_tuple().exponent < -2:
            raise InvalidOperation
        return int(value * 100)
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationFailure(
            "bad_count", f"Enter a valid nonnegative {name.upper()} count",
            {name: "nonnegative amount with at most two decimals"})


@bp.get("/balances")
def balances_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("balances.html")


@bp.get("/api/balances")
def balances():
    """Present-state view: SAFE, every signout (open and closed),
    per-signout blockers, and cash-outstanding totals. All derived from one
    replay; nothing here is stored."""
    guard = require_admin()
    if guard:
        return guard
    res = repo.replay_projected(g.db)
    summaries = signouts.summaries(g.db, res)

    def state_for(account_id):
        st = res.states.get(account_id)
        if st is None:
            return {"usd": 0, "mxn": 0, "basis_usd": "0"}
        return {"usd": st.usd / 100, "mxn": st.mxn / 100,
                "basis_usd": str(st.basis)}

    signouts_out = []
    for voucher_id, summary in sorted(summaries.items()):
        signouts_out.append({
            "voucher_id": voucher_id,
            "recipient": summary.get("recipient"),
            "purpose": summary.get("purpose"),
            "open": summary["open"],
            "reasons": summary.get("reasons", []),
            "balance": state_for(voucher_id),
        })

    safe = res.states.get(SAFE)
    signout_usd = sum(s["balance"]["usd"] for s in signouts_out)
    signout_mxn = sum(s["balance"]["mxn"] for s in signouts_out)
    open_signout_value = sum(
        (state.value_usd for account, state in res.states.items()
         if account in summaries and summaries[account]["open"]), Decimal(0))
    return ok({
        "safe": state_for(SAFE),
        "signouts": signouts_out,
        "totals": {
            "signout_usd": signout_usd,
            "signout_mxn": signout_mxn,
            "open_signout_value_usd": str(open_signout_value),
            "tracked_usd": (safe.usd if safe else 0) / 100 + signout_usd,
            "tracked_mxn": (safe.mxn if safe else 0) / 100 + signout_mxn,
            "open_count": sum(1 for s in signouts_out if s["open"]),
        },
        "ledger_revision": g.db.execute(
            "SELECT ledger_revision FROM ledger_state WHERE singleton_id=1"
        ).fetchone()[0],
    })


@bp.post("/api/balances/safe-count")
def safe_count():
    """Compare a physical count to the books; nothing persisted
    (spec 10)."""
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    try:
        usd = _count_minor(data, "usd")
        mxn = _count_minor(data, "mxn")
    except ValidationFailure as exc:
        return err(exc.code, exc.message, 400, exc.fields)
    res = repo.replay_projected(g.db)
    st = res.states[SAFE]
    return ok({
        "book": {"usd": st.usd / 100, "mxn": st.mxn / 100,
                 "basis_usd": str(st.basis)},
        "count": {"usd": usd / 100, "mxn": mxn / 100},
        "delta": {"usd": (usd - st.usd) / 100,
                  "mxn": (mxn - st.mxn) / 100},
        "ledger_revision": g.db.execute(
            "SELECT ledger_revision FROM ledger_state WHERE singleton_id=1"
        ).fetchone()[0],
    })


@bp.post("/api/balances/safe-count/correct")
def correct_safe_count():
    """Commit all currency differences from one count as one command."""
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    try:
        submission = str(uuid.UUID(data["submission_id"]))
        date = data["date"]
        calendar_date.fromisoformat(date)
        expected_revision = int(data["expected_ledger_revision"])
        usd = _count_minor(data, "usd")
        mxn = _count_minor(data, "mxn")
        rate_value = data.get("assigned_rate_milli")
        rate = int(rate_value) if rate_value not in (None, "") else None
        if rate is not None and rate <= 0:
            raise ValueError("assigned_rate_milli")
    except ValidationFailure as exc:
        return err(exc.code, exc.message, 400, exc.fields)
    except KeyError as exc:
        return err("bad_request", f"Missing field: {exc}", 400)
    except (ValueError, TypeError) as exc:
        return err("bad_request", f"Invalid field: {exc}", 400)

    result = run_command(
        g.db, submission, "correct_safe_count",
        lambda connection: prep.create_safe_count_corrections(
            connection, submission_id=submission, date=date,
            expected_ledger_revision=expected_revision,
            counted_usd_cents=usd, counted_mxn_centavos=mxn,
            reason=data.get("reason", ""),
            assigned_rate_milli=rate,
            document_id=data.get("document_id") or None))
    return ok(result)
