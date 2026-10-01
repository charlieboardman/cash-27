"""Derived signout state.

The ledger, paperwork, receipt reviews, and explicit closeout confirmation
determine whether a signout is open. There is no stored open/closed flag.
"""

from __future__ import annotations

from . import repo


UNRESOLVED_PACKAGE_STATES = ("submitted", "in_review", "needs_changes")


def summaries(conn, replayed=None) -> dict[str, dict]:
    """Replay once and derive every signout's current workflow state."""
    # Imported here: closeouts and expense_drafts both depend on this module.
    from . import expense_drafts
    from .closeouts import confirmation
    replayed = replayed or repo.replay_projected(conn)
    result = {}
    vouchers = conn.execute(
        "SELECT * FROM vouchers WHERE type='signout' ORDER BY voucher_id"
    ).fetchall()
    for voucher in vouchers:
        voucher_id = voucher["voucher_id"]
        state = replayed.states.get(voucher_id)
        usd = state.usd if state else 0
        mxn = state.mxn if state else 0
        basis = str(state.basis) if state else "0"
        has_active_signout = bool(conn.execute(
            "SELECT 1 FROM transactions WHERE voucher_id=?"
            " AND txn_type='signout' AND posting_state='active' LIMIT 1",
            (voucher_id,),
        ).fetchone())
        has_voucher = bool(conn.execute(
            "SELECT 1 FROM transactions t JOIN transaction_documents td"
            " ON td.txn_id=t.txn_id WHERE t.voucher_id=?"
            " AND t.txn_type='signout' AND t.posting_state='active'"
            " AND td.role='voucher' LIMIT 1", (voucher_id,)
        ).fetchone())
        pending_addenda = conn.execute(
            "SELECT COUNT(*) FROM addenda a JOIN transactions t ON"
            " t.addendum_id=a.addendum_id WHERE a.voucher_id=?"
            " AND a.status!='void' AND t.posting_state='active'"
            " AND (a.document_id IS NULL OR a.status!='complete')",
            (voucher_id,),
        ).fetchone()[0]
        draft = expense_drafts.stored(conn, voucher_id)
        work = expense_drafts.open_work(draft[0]) if draft else {"open_rows": 0, "submitted": False}
        open_claims = work["open_rows"]
        unresolved_packages = int(work["submitted"])
        closeout = confirmation(conn, voucher_id)
        reasons = []
        if usd or mxn:
            reasons.append("cash remaining" if usd >= 0 and mxn >= 0
                           else "cash balance needs attention")
        if (state and state.basis and not mxn) or any(
                claim.account == voucher_id for claim in replayed.open_claims):
            reasons.append("currency value needs reconciliation")
        if not has_voucher:
            reasons.append("voucher needed")
        if pending_addenda:
            reasons.append("addendum needed")
        if open_claims:
            reasons.append("receipts in progress")
        if unresolved_packages:
            reasons.append("receipt submission in progress")
        if not closeout:
            reasons.append("closeout needed")
        void = voucher["status"] == "void" or not has_active_signout
        is_open = not void and bool(reasons)
        result[voucher_id] = {
            "voucher_id": voucher_id,
            "recipient": voucher["recipient"],
            "purpose": voucher["purpose"],
            "usd_cents": usd,
            "mxn_centavos": mxn,
            "basis_usd": basis,
            "has_voucher": has_voucher,
            "pending_addenda": pending_addenda,
            "open_claims": open_claims,
            "unresolved_packages": unresolved_packages,
            "closeout": closeout,
            "reasons": reasons,
            "status": "void" if void else ("open" if is_open else "closed"),
            "open": is_open,
            "read_only": void or not is_open,
        }
    return result


def summary(conn, voucher_id: str, replayed=None) -> dict | None:
    return summaries(conn, replayed).get(voucher_id)
