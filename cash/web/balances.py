"""Current balances page (spec 10): replayed SAFE/signout state,
physical SAFE count comparison, and one-glance conservation totals.
The bank is an external source, not a tracked pool; withdrawals inject
cash and no bank balance is tracked or shown."""

from __future__ import annotations

from datetime import date as calendar_date
from decimal import Decimal
import uuid

from flask import Blueprint, g, render_template

from .. import prep, repo, signouts
from ..commands import run_command
from ..model import SAFE
from ..money import to_minor, to_text
from .app import admin_required, err, json_body, ok
from .qr_print import qr_svg

bp = Blueprint("balances", __name__)


@bp.get("/balances")
@admin_required
def balances_page():
    return render_template("balances.html")


@bp.get("/api/balances")
@admin_required
def balances():
    """Present-state view: SAFE, every active signout (open and closed),
    per-signout blockers, and cash-outstanding totals. All derived from one
    replay; nothing here is stored."""
    res = repo.replay_projected(g.db)
    summaries = signouts.summaries(g.db, res)

    def state_for(account_id):
        st = res.states.get(account_id)
        if st is None:
            return {"usd": "0.00", "mxn": "0.00", "basis_usd": "0"}
        return {"usd": to_text(st.usd), "mxn": to_text(st.mxn),
                "basis_usd": str(st.basis)}

    signouts_out = []
    for voucher_id, summary in sorted(summaries.items()):
        if summary["status"] == "void":
            continue
        signouts_out.append({
            "voucher_id": voucher_id,
            "recipient": summary.get("recipient"),
            "purpose": summary.get("purpose"),
            "open": summary["open"],
            "reasons": summary.get("reasons", []),
            "balance": state_for(voucher_id),
        })

    safe = res.states.get(SAFE)
    signout_states = [res.states.get(voucher_id) for voucher_id in summaries]
    signout_usd = sum(st.usd for st in signout_states if st)
    signout_mxn = sum(st.mxn for st in signout_states if st)
    open_signout_value = sum(
        (state.value_usd for account, state in res.states.items()
         if account in summaries and summaries[account]["open"]), Decimal(0))
    return ok({
        "safe": state_for(SAFE),
        "signouts": signouts_out,
        "totals": {
            "signout_usd": to_text(signout_usd),
            "signout_mxn": to_text(signout_mxn),
            "open_signout_value_usd": str(open_signout_value),
            "tracked_usd": to_text((safe.usd if safe else 0) + signout_usd),
            "tracked_mxn": to_text((safe.mxn if safe else 0) + signout_mxn),
            "open_count": sum(1 for s in signouts_out if s["open"]),
        },
        "ledger_revision": repo.ledger_revision(g.db),
    })


@bp.post("/api/balances/safe-count")
@admin_required
def safe_count():
    """Compare a physical count to the books; nothing persisted
    (spec 10)."""
    data = json_body("usd", "mxn")
    usd = to_minor(data.get("usd"), "counted USD", allow_negative=False)
    mxn = to_minor(data.get("mxn"), "counted MXN", allow_negative=False)
    res = repo.replay_projected(g.db)
    st = res.states[SAFE]
    return ok({
        "book": {"usd": to_text(st.usd), "mxn": to_text(st.mxn),
                 "basis_usd": str(st.basis)},
        "count": {"usd": to_text(usd), "mxn": to_text(mxn)},
        "delta": {"usd": to_text(usd - st.usd), "mxn": to_text(mxn - st.mxn)},
        "ledger_revision": repo.ledger_revision(g.db),
    })


@bp.post("/api/balances/safe-count/correct")
@admin_required
def correct_safe_count():
    """Commit all currency differences from one count as one command."""
    data = json_body("submission_id", "date", "expected_ledger_revision", "usd",
                     "mxn", "assigned_rate_milli", "reason")
    usd = to_minor(data.get("usd"), "counted USD", allow_negative=False)
    mxn = to_minor(data.get("mxn"), "counted MXN", allow_negative=False)
    try:
        submission = str(uuid.UUID(data["submission_id"]))
        date = data["date"]
        calendar_date.fromisoformat(date)
        expected_revision = int(data["expected_ledger_revision"])
        rate_value = data.get("assigned_rate_milli")
        rate = int(rate_value) if rate_value not in (None, "") else None
        if rate is not None and rate <= 0:
            raise ValueError("assigned_rate_milli")
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
            assigned_rate_milli=rate))
    return ok(result)


@bp.get("/balances/count/<int:count_id>/print")
@admin_required
def count_sheet(count_id):
    """The count's sheet for signatures, prefilled from the ledger: what was
    counted, what the records said, and the difference."""
    rows = prep.safe_count_rows(g.db, count_id)
    if not rows or rows[0]["txn_id"] != count_id:
        return err("not_found", "Safe count not found", 404)
    replayed = repo.replay_projected(g.db)
    after = replayed.movements[rows[-1]["movement_id"]].safe_balance_after
    sign = lambda row: 1 if row["to_account"] == SAFE else -1
    delta_usd = sum(sign(row) * row["usd_cents"] for row in rows)
    delta_mxn = sum(sign(row) * row["mxn_centavos"] for row in rows)
    shown = lambda cents: f"{cents / 100:,.2f}"
    return render_template(
        "count_sheet.html", code=f"Txn-{count_id}", qr=qr_svg(f"Txn-{count_id}"),
        date=rows[0]["date"], reason=rows[0]["description"],
        counted={"usd": shown(after.usd), "mxn": shown(after.mxn)},
        book={"usd": shown(after.usd - delta_usd), "mxn": shown(after.mxn - delta_mxn)},
        delta={"usd": shown(delta_usd), "mxn": shown(delta_mxn)})
