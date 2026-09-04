"""Derived signout state.

The ledger and workflow facts are authoritative.  ``vouchers.status`` remains
for compatibility with old exports, but callers must not use it to decide
whether a signout is open.
"""

from __future__ import annotations

from . import repo


UNRESOLVED_PACKAGE_STATES = ("submitted", "in_review", "needs_changes")


def summaries(conn, replayed=None) -> dict[str, dict]:
    """Replay once and derive every signout's current workflow state."""
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
        open_claims = conn.execute(
            "SELECT COUNT(*) FROM signout_expense_claims WHERE voucher_id=?"
            " AND state!='accepted'", (voucher_id,),
        ).fetchone()[0]
        unresolved_packages = conn.execute(
            "SELECT COUNT(*) FROM signout_reconciliation_packages"
            " WHERE voucher_id=? AND status IN ('submitted','in_review',"
            " 'needs_changes')", (voucher_id,),
        ).fetchone()[0]
        reasons = []
        if usd or mxn:
            reasons.append("cash remaining" if usd >= 0 and mxn >= 0
                           else "cash balance needs attention")
        if not has_voucher:
            reasons.append("voucher needed")
        if pending_addenda:
            reasons.append("addendum needed")
        if open_claims:
            reasons.append("receipts in progress")
        if unresolved_packages:
            reasons.append("receipt submission in progress")
        void = voucher["status"] == "void"
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
            "reasons": reasons,
            "status": "void" if void else ("open" if is_open else "closed"),
            "open": is_open,
            "read_only": void or not is_open,
        }
    return result


def summary(conn, voucher_id: str, replayed=None) -> dict | None:
    return summaries(conn, replayed).get(voucher_id)


def sync_compatibility_status(conn, voucher_id: str) -> dict | None:
    """Keep the legacy status column useful without making it authoritative."""
    derived = summary(conn, voucher_id)
    if not derived or derived["status"] == "void":
        return derived
    stored = "pending" if derived["open"] else "complete"
    conn.execute(
        "UPDATE vouchers SET status=?, updated_at="
        " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE voucher_id=?"
        " AND status!=?", (stored, voucher_id, stored)
    )
    return derived
