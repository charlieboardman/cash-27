"""Ledger view APIs (spec 10): read-only replay-order transaction
browsing, header balances, SAFE count comparison, corrections drill."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, g, render_template, request

from .. import repo
from ..model import SAFE
from .app import ok, err, require_admin

bp = Blueprint("ledger", __name__)


@bp.get("/ledger")
def ledger_page():
    guard = require_admin()
    if guard:
        return guard
    return render_template("ledger.html")


@bp.get("/api/ledger")
def ledger():
    guard = require_admin()
    if guard:
        return guard
    res = repo.replay_projected(g.db)

    q = request.args
    fy = q.get("fy")
    account = q.get("account")
    voucher = q.get("voucher")
    ttype = q.get("type")

    rows = []
    for t in g.db.execute(
            "SELECT * FROM transactions ORDER BY date, same_day_seq,"
            " txn_id"):
        if fy and not t["date"].startswith(fy):
            continue
        if account and account not in (t["from_account"], t["to_account"]):
            continue
        if voucher and t["voucher_id"] != voucher:
            continue
        if ttype and t["txn_type"] != ttype:
            continue
        d = dict(t)
        d["usd"] = t["usd_cents"] / 100
        d["mxn"] = t["mxn_centavos"] / 100
        mid = t["movement_id"]
        mres = res.movements.get(mid)
        d["calc_usd"] = (str(mres.calc_usd_by_row.get(str(t["txn_id"])))
                         if mres else None)
        rows.append(d)

    balances = {}
    for acct, st in res.states.items():
        balances[acct] = {"usd": st.usd / 100, "mxn": st.mxn / 100,
                          "basis_usd": str(st.basis)}
    open_signouts = [r["voucher_id"] for r in g.db.execute(
        "SELECT voucher_id FROM vouchers WHERE type='signout'"
        " AND status='pending'")]
    unallocated = g.db.execute(
        "SELECT COUNT(*) FROM transactions WHERE to_account="
        "'UNALLOCATED_EXPENSE' AND posting_state='active'").fetchone()[0]
    return ok({"transactions": rows, "balances": balances,
               "open_signouts": open_signouts,
               "outstanding_unallocated": unallocated,
               "open_claims": [{"account": c.account,
                                "mxn_unfunded": c.mxn_unfunded / 100}
                               for c in res.open_claims]})


@bp.post("/api/ledger/safe-count")
def safe_count():
    """Compare a physical count to the books; nothing persisted
    (spec 13)."""
    guard = require_admin()
    if guard:
        return guard
    data = request.get_json(force=True)
    try:
        usd = int(Decimal(str(data.get("usd", "0"))) * 100)
        mxn = int(Decimal(str(data.get("mxn", "0"))) * 100)
    except InvalidOperation:
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
