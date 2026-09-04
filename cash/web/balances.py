"""Current balances page (spec 10): replayed SAFE/signout state,
physical SAFE count comparison, and one-glance conservation totals.
The bank is an external source, not a tracked pool; withdrawals inject
cash and no bank balance is tracked or shown."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, render_template, request

from .. import repo, signouts
from ..model import SAFE
from .app import err, ok, require_admin

bp = Blueprint("balances", __name__)


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
    return ok({
        "safe": state_for(SAFE),
        "signouts": signouts_out,
        "totals": {
            "signout_usd": signout_usd,
            "signout_mxn": signout_mxn,
            "tracked_usd": (safe.usd if safe else 0) / 100 + signout_usd,
            "tracked_mxn": (safe.mxn if safe else 0) / 100 + signout_mxn,
            "open_count": sum(1 for s in signouts_out if s["open"]),
        },
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
        usd_value = Decimal(str(data.get("usd", "0")))
        mxn_value = Decimal(str(data.get("mxn", "0")))
        if usd_value.as_tuple().exponent < -2 or \
                mxn_value.as_tuple().exponent < -2:
            raise InvalidOperation
        usd = int(usd_value * 100)
        mxn = int(mxn_value * 100)
    except (InvalidOperation, ValueError):
        return err("bad_request", "Invalid amounts", 400)
    res = repo.replay_projected(g.db)
    st = res.states[SAFE]
    return ok({
        "book": {"usd": st.usd / 100, "mxn": st.mxn / 100,
                 "basis_usd": str(st.basis)},
        "count": {"usd": usd / 100, "mxn": mxn / 100},
        "delta": {"usd": (usd - st.usd) / 100,
                  "mxn": (mxn - st.mxn) / 100},
    })