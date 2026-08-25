"""Signee package workflows (spec 12): claims CRUD, package
submit/withdraw, admin review + atomic acceptance/return."""

from __future__ import annotations

import uuid

from .commands import ValidationFailure
from .model import Row
from . import repo


def _voucher(conn, voucher_id):
    v = conn.execute(
        "SELECT type, status FROM vouchers WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    if not v or v["type"] != "signout":
        raise ValidationFailure("not_signout", "Unknown signout",
                                {"voucher_id": "not a signout"})
    return v


def _unresolved_package(conn, voucher_id):
    return conn.execute(
        "SELECT * FROM signout_reconciliation_packages WHERE"
        " voucher_id = ? AND status IN"
        " ('submitted','in_review','needs_changes')",
        (voucher_id,)).fetchone()


def create_claim(conn, *, voucher_id, document_id, claim_date, vendor,
                 description, face_value_minor, face_currency,
                 usd_paid_cents, mxn_paid_centavos,
                 mxn_change_centavos, usd_change_cents, notes) -> dict:
    """Signee receipt draft (spec 12). Claims do not participate in
    ledger replay."""
    _voucher(conn, voucher_id)
    d = conn.execute(
        "SELECT state FROM documents WHERE document_id = ?",
        (document_id,)).fetchone()
    if not d or d["state"] not in ("inbox", "signee_draft"):
        raise ValidationFailure("bad_document", "Document unavailable")
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO signout_expense_claims (claim_id, voucher_id,"
        " document_id, claim_date, vendor, description,"
        " face_value_minor, face_currency, usd_paid_cents,"
        " mxn_paid_centavos, mxn_change_centavos, usd_change_cents,"
        " notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, voucher_id, document_id, claim_date, vendor, description,
         face_value_minor, face_currency, usd_paid_cents,
         mxn_paid_centavos, mxn_change_centavos, usd_change_cents,
         notes))
    conn.execute(
        "UPDATE documents SET state='signee_draft', voucher_id=?"
        " WHERE document_id=?", (voucher_id, document_id))
    return {"claim_id": cid}


def submit_package(conn, *, voucher_id, claim_ids) -> dict:
    """Selected drafts become one package (spec 12); one unresolved
    package per signout."""
    _voucher(conn, voucher_id)
    if _unresolved_package(conn, voucher_id):
        raise ValidationFailure("package_open",
                                "One unresolved package already exists")
    if not claim_ids:
        raise ValidationFailure("empty_package", "Select claims")
    pid = str(uuid.uuid4())
    cur = conn.execute(
        "SELECT COALESCE(MAX(seq),0)+1 FROM"
        " signout_reconciliation_packages WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    seq = cur[0]
    conn.execute(
        "INSERT INTO signout_reconciliation_packages (package_id,"
        " voucher_id, seq, status, submitted_at) VALUES (?,?,?,"
        " 'submitted', strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
        (pid, voucher_id, seq))
    n = 0
    for cid in claim_ids:
        r = conn.execute(
            "UPDATE signout_expense_claims SET package_id=?,"
            " state='submitted' WHERE claim_id=? AND voucher_id=?"
            " AND state='draft'", (pid, cid, voucher_id))
        n += r.rowcount
        conn.execute(
            "UPDATE documents SET state='package_submitted' WHERE"
            " document_id = (SELECT document_id FROM"
            " signout_expense_claims WHERE claim_id=?)", (cid,))
    if n != len(claim_ids):
        raise ValidationFailure("claims_not_draft",
                                f"Only {n}/{len(claim_ids)} are drafts")
    return {"package_id": pid, "seq": seq}


def withdraw_package(conn, *, voucher_id, package_id) -> dict:
    p = _unresolved_package(conn, voucher_id)
    if not p or p["package_id"] != package_id:
        raise ValidationFailure("no_package", "No such open package")
    if p["status"] == "in_review":
        raise ValidationFailure("in_review", "Under active review")
    conn.execute(
        "UPDATE documents SET state='signee_draft' WHERE document_id IN"
        " (SELECT document_id FROM signout_expense_claims WHERE"
        " package_id=?)", (package_id,))
    conn.execute(
        "UPDATE signout_expense_claims SET package_id=NULL,"
        " state='draft' WHERE package_id=?", (package_id,))
    conn.execute(
        "UPDATE signout_reconciliation_packages SET"
        " status='withdrawn', version=version+1,"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        " WHERE package_id=?", (package_id,))
    return {"package_id": package_id, "status": "withdrawn"}


def verify_claim(conn, *, claim_id, expected_content_version,
                 admin_note="") -> dict:
    """Admin marks a claim verified at its current content version.
    Only claims inside a submitted package can be verified (spec 11)."""
    c = conn.execute(
        "SELECT content_version, package_id, state FROM"
        " signout_expense_claims WHERE claim_id = ?",
        (claim_id,)).fetchone()
    if not c:
        raise ValidationFailure("not_found", "Claim not found")
    if not c["package_id"]:
        raise ValidationFailure("not_submitted",
                                "Claim is not in a submitted package")
    if c["content_version"] != expected_content_version:
        from .repo import ConflictError
        raise ConflictError(
            f"claim {claim_id} content_version "
            f"{c['content_version']} != expected "
            f"{expected_content_version}")
    conn.execute(
        "UPDATE signout_expense_claims SET state='verified',"
        " verified_version=content_version, admin_note=?,"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        " WHERE claim_id=?", (admin_note, claim_id))
    return {"claim_id": claim_id, "state": "verified"}


def return_package(conn, *, package_id, admin_summary,
                   item_notes=None) -> dict:
    """Review state change only; no ledger effects (spec 14). The
    summary plus per-item correction notes (spec 11) flag the claims
    the signee must fix."""
    item_notes = item_notes or {}
    for claim_id, note in item_notes.items():
        conn.execute(
            "UPDATE signout_expense_claims SET state='flagged',"
            " admin_note=?, updated_at="
            " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE claim_id=?",
            (note, claim_id))
    conn.execute(
        "UPDATE signout_reconciliation_packages SET"
        " status='needs_changes', admin_summary=?, version=version+1,"
        " reviewed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE"
        " package_id=?", (admin_summary, package_id))
    return {"package_id": package_id, "status": "needs_changes",
            "flagged": list(item_notes)}


def accept_package(conn, *, submission_id, package_id) -> dict:
    """Atomic acceptance (spec 11, 14): every claim verified at its
    current version; allocations summed to claim; all rows or none."""
    p = conn.execute(
        "SELECT * FROM signout_reconciliation_packages WHERE"
        " package_id=?", (package_id,)).fetchone()
    if not p or p["status"] not in ("submitted", "in_review", "needs_changes"):
        raise ValidationFailure("not_open", "Package not open")
    claims = conn.execute(
        "SELECT * FROM signout_expense_claims WHERE package_id=?",
        (package_id,)).fetchall()
    if not claims:
        raise ValidationFailure("empty", "No claims")
    for c in claims:
        if c["state"] != "verified" or \
                c["verified_version"] != c["content_version"]:
            raise ValidationFailure(
                "unverified", f"Claim {c['claim_id']} not verified at "
                f"current version")
    new_ids = []
    for c in claims:
        allocs = conn.execute(
            "SELECT * FROM signout_claim_allocations WHERE claim_id=?"
            " ORDER BY line_no", (c["claim_id"],)).fetchall()
        if not allocs:
            raise ValidationFailure(
                "no_allocations",
                f"Claim {c['claim_id']} has no allocations; final "
                f"expenses require an expense account and class "
                f"(spec 6)")
        alloc_usd = sum(a["usd_cents"] for a in allocs)
        alloc_mxn = sum(a["mxn_centavos"] for a in allocs)
        net_usd = c["usd_paid_cents"] - c["usd_change_cents"]
        net_mxn = c["mxn_paid_centavos"] - c["mxn_change_centavos"]
        if alloc_usd != net_usd or alloc_mxn != net_mxn:
            raise ValidationFailure(
                "allocation_mismatch",
                f"Claim {c['claim_id']}: allocations must sum to "
                f"submitted facts")
        for a in allocs:
            if not a["expense_account"] or not a["class_code"]:
                raise ValidationFailure(
                    "incomplete_allocation",
                    f"Claim {c['claim_id']}: every allocation needs an "
                    f"expense account and class")
        cur = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM receipts WHERE"
            " voucher_id=?", (p["voucher_id"],)).fetchone()
        rid = f"{p['voucher_id']}-R{cur[0]}"
        conn.execute(
            "INSERT INTO receipts (receipt_id, voucher_id, seq,"
            " document_id, face_value_minor, face_currency, vendor,"
            " reviewed_date) VALUES (?,?,?,?,?,?,?,"
            " strftime('%Y-%m-%d','now'))",
            (rid, p["voucher_id"], cur[0], c["document_id"],
             c["face_value_minor"], c["face_currency"], c["vendor"]))
        mv = str(uuid.uuid4())
        rows = []
        acct = p["voucher_id"]
        # Each claim is its own movement; claims on the same date need
        # distinct same-day sequences so replay order is truthful
        # (spec 13). New events go after existing ones on that date.
        cur = conn.execute(
            "SELECT COALESCE(MAX(same_day_seq),-1)+1 FROM transactions"
            " WHERE date=? AND (from_account=? OR to_account=?)",
            (c["claim_date"], acct, acct)).fetchone()
        seq = cur[0]
        for line, a in enumerate(allocs):
            face = (a["face_value_minor"] if a["face_value_minor"]
                    else None)
            rows.append(Row(
                row_id="pending", movement_id=mv,
                date=c["claim_date"], same_day_seq=seq,
                allocation_line=line, txn_type="expense",
                from_account=acct, to_account=a["expense_account"],
                usd=a["usd_cents"], mxn=a["mxn_centavos"],
                class_code=a["class_code"],
                receipt_value_mxn=(face
                                   if c["face_currency"] == "MXN"
                                   else None),
                receipt_value_usd=(face
                                   if c["face_currency"] == "USD"
                                   else None),
                voucher_id=p["voucher_id"], receipt_id=rid))
        ids = repo.insert_rows(conn, submission_id, rows)
        new_ids.append({"claim_id": c["claim_id"], "receipt_id": rid,
                        "txn_ids": ids})
        conn.execute(
            "UPDATE signout_expense_claims SET state='accepted',"
            " final_receipt_id=?, final_movement_id=? WHERE claim_id=?",
            (rid, mv, c["claim_id"]))
        conn.execute(
            "UPDATE documents SET state='filed', doc_type='receipt'"
            " WHERE document_id=?", (c["document_id"],))
    conn.execute(
        "UPDATE signout_reconciliation_packages SET status='accepted',"
        " accepting_submission_id=?,"
        " reviewed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        " WHERE package_id=?", (submission_id, package_id))
    return {"package_id": package_id, "accepted": new_ids}
