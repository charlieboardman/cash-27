"""Signee package workflows (spec 12): claims CRUD, package
submit/withdraw, admin review + atomic acceptance/return."""

from __future__ import annotations

from datetime import date
import uuid

from .commands import ValidationFailure
from .model import Row
from . import repo
from . import signouts


def _voucher(conn, voucher_id):
    v = conn.execute(
        "SELECT type, status FROM vouchers WHERE voucher_id = ?",
        (voucher_id,)).fetchone()
    if not v or v["type"] != "signout":
        raise ValidationFailure("not_signout", "Unknown signout",
                                {"voucher_id": "not a signout"})
    derived = signouts.summary(conn, voucher_id)
    if not derived or derived["read_only"]:
        raise ValidationFailure(
            "voucher_read_only",
            "This signout is closed and its receipt workspace is read-only")
    return v


def _receipt_faces(*, face_usd_cents=None, face_mxn_centavos=None):
    return int(face_usd_cents or 0), int(face_mxn_centavos or 0)


def _validate_claim_facts(*, claim_date, vendor, description,
                          face_usd_cents=None, face_mxn_centavos=None,
                          usd_paid_cents, mxn_paid_centavos,
                          mxn_change_centavos, usd_change_cents):
    try:
        date.fromisoformat(claim_date)
    except (TypeError, ValueError) as exc:
        raise ValidationFailure("bad_date", "Enter a valid receipt date") from exc
    if not str(vendor).strip():
        raise ValidationFailure("vendor_required", "Vendor is required")
    if not str(description).strip():
        raise ValidationFailure("description_required",
                                "Receipt description is required")
    face_usd, face_mxn = _receipt_faces(
        face_usd_cents=face_usd_cents,
        face_mxn_centavos=face_mxn_centavos)
    if face_usd < 0 or face_mxn < 0 or not (face_usd or face_mxn):
        raise ValidationFailure(
            "bad_face_value", "Enter a positive receipt value")
    facts = (usd_paid_cents, mxn_paid_centavos,
             mxn_change_centavos, usd_change_cents)
    if any(value < 0 for value in facts):
        raise ValidationFailure("negative_tender",
                                "Cash and change fields cannot be negative")
    net_usd = usd_paid_cents - usd_change_cents
    net_mxn = mxn_paid_centavos - mxn_change_centavos
    if net_usd <= 0 and net_mxn <= 0:
        raise ValidationFailure(
            "no_settlement",
            "Cash handed over minus change must be greater than zero")


def _unresolved_package(conn, voucher_id):
    return conn.execute(
        "SELECT * FROM signout_reconciliation_packages WHERE"
        " voucher_id = ? AND status IN"
        " ('submitted','in_review','needs_changes')",
        (voucher_id,)).fetchone()


def _claim_payment(payment_mode, face_usd, face_mxn, usd, mxn, usd_change, mxn_change):
    if payment_mode == "receipt_currency":
        return face_usd, face_mxn, 0, 0
    if payment_mode != "cash":
        raise ValidationFailure("bad_payment_mode", "Choose receipt currency or cash payment details")
    return usd, mxn, usd_change, mxn_change


def create_claim(conn, *, voucher_id, document_id, claim_date, vendor,
                 description,
                 face_usd_cents=None, face_mxn_centavos=None,
                 usd_paid_cents=0, mxn_paid_centavos=0,
                 mxn_change_centavos=0, usd_change_cents=0,
                 notes="", payment_mode="cash") -> dict:
    """Signee receipt draft (spec 12). Claims do not participate in
    ledger replay."""
    _voucher(conn, voucher_id)
    faces = _receipt_faces(face_usd_cents=face_usd_cents,
                           face_mxn_centavos=face_mxn_centavos)
    usd_paid_cents, mxn_paid_centavos, usd_change_cents, mxn_change_centavos = _claim_payment(
        payment_mode, *faces, usd_paid_cents, mxn_paid_centavos,
        usd_change_cents, mxn_change_centavos)
    _validate_claim_facts(
        claim_date=claim_date, vendor=vendor, description=description,
        face_usd_cents=face_usd_cents,
        face_mxn_centavos=face_mxn_centavos,
        usd_paid_cents=usd_paid_cents,
        mxn_paid_centavos=mxn_paid_centavos,
        mxn_change_centavos=mxn_change_centavos,
        usd_change_cents=usd_change_cents)
    d = conn.execute(
        "SELECT state FROM documents WHERE document_id = ?",
        (document_id,)).fetchone()
    if not d or d["state"] not in ("inbox", "signee_draft"):
        raise ValidationFailure("bad_document", "Document unavailable")
    face_usd, face_mxn = _receipt_faces(
        face_usd_cents=face_usd_cents,
        face_mxn_centavos=face_mxn_centavos)
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO signout_expense_claims (claim_id, voucher_id,"
        " document_id, claim_date, vendor, description,"
        " usd_paid_cents,"
        " mxn_paid_centavos, mxn_change_centavos, usd_change_cents,"
        " notes, face_usd_cents, face_mxn_centavos, payment_mode)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, voucher_id, document_id, claim_date, vendor, description,
         usd_paid_cents,
         mxn_paid_centavos, mxn_change_centavos, usd_change_cents,
         notes, face_usd or None, face_mxn or None, payment_mode))
    conn.execute(
        "UPDATE documents SET state='signee_draft', voucher_id=?"
        " WHERE document_id=?", (voucher_id, document_id))
    return {"claim_id": cid}


def update_claim(conn, *, voucher_id, claim_id, expected_content_version,
                 document_id, claim_date, vendor, description,
                 face_usd_cents=None, face_mxn_centavos=None,
                 usd_paid_cents=0,
                 mxn_paid_centavos=0, mxn_change_centavos=0,
                 usd_change_cents=0, notes="", payment_mode="cash") -> dict:
    """Correct an editable draft or a flagged claim in a returned package."""
    _voucher(conn, voucher_id)
    faces = _receipt_faces(face_usd_cents=face_usd_cents,
                           face_mxn_centavos=face_mxn_centavos)
    usd_paid_cents, mxn_paid_centavos, usd_change_cents, mxn_change_centavos = _claim_payment(
        payment_mode, *faces, usd_paid_cents, mxn_paid_centavos,
        usd_change_cents, mxn_change_centavos)
    claim = conn.execute(
        "SELECT c.*, p.status AS package_status FROM"
        " signout_expense_claims c LEFT JOIN"
        " signout_reconciliation_packages p ON p.package_id=c.package_id"
        " WHERE c.claim_id=? AND c.voucher_id=?",
        (claim_id, voucher_id)).fetchone()
    if not claim:
        raise ValidationFailure("not_found", "Receipt draft not found")
    editable = (claim["state"] == "draft" and not claim["package_id"]) or (
        claim["state"] == "flagged" and
        claim["package_status"] == "needs_changes")
    if not editable:
        raise ValidationFailure(
            "claim_locked",
            "This receipt cannot be edited while its submission is being reviewed")
    if claim["content_version"] != expected_content_version:
        from .repo import ConflictError
        raise ConflictError(
            f"claim {claim_id} content_version {claim['content_version']}"
            f" != expected {expected_content_version}")
    _validate_claim_facts(
        claim_date=claim_date, vendor=vendor, description=description,
        face_usd_cents=face_usd_cents,
        face_mxn_centavos=face_mxn_centavos,
        usd_paid_cents=usd_paid_cents,
        mxn_paid_centavos=mxn_paid_centavos,
        mxn_change_centavos=mxn_change_centavos,
        usd_change_cents=usd_change_cents)
    if document_id != claim["document_id"]:
        replacement = conn.execute(
            "SELECT state, voucher_id FROM documents WHERE document_id=?",
            (document_id,)).fetchone()
        if not replacement or replacement["state"] not in (
                "inbox", "signee_draft"):
            raise ValidationFailure("bad_document",
                                    "Replacement document is unavailable")
        if replacement["voucher_id"] not in (None, voucher_id):
            raise ValidationFailure("bad_document",
                                    "Replacement belongs to another signout")
        conn.execute(
            "UPDATE documents SET state='discarded' WHERE document_id=?",
            (claim["document_id"],))
        conn.execute(
            "UPDATE documents SET state='signee_draft', voucher_id=?"
            " WHERE document_id=?", (voucher_id, document_id))
    face_usd, face_mxn = _receipt_faces(
        face_usd_cents=face_usd_cents,
        face_mxn_centavos=face_mxn_centavos)
    conn.execute(
        "UPDATE signout_expense_claims SET document_id=?, claim_date=?,"
        " vendor=?, description=?,"
        " face_usd_cents=?, face_mxn_centavos=?, usd_paid_cents=?,"
        " mxn_paid_centavos=?, mxn_change_centavos=?,"
        " usd_change_cents=?, notes=?, payment_mode=?, content_version=content_version+1,"
        " verified_version=NULL, updated_at="
        " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE claim_id=?",
        (document_id, claim_date, str(vendor).strip(),
         str(description).strip(),
         face_usd or None, face_mxn or None,
         usd_paid_cents, mxn_paid_centavos, mxn_change_centavos,
         usd_change_cents, notes, payment_mode, claim_id))
    conn.execute("DELETE FROM signout_claim_allocations WHERE claim_id=?",
                 (claim_id,))
    return {"claim_id": claim_id,
            "content_version": expected_content_version + 1,
            "state": claim["state"]}


def delete_claim(conn, *, voucher_id, claim_id,
                 expected_content_version) -> dict:
    """Discard an unsubmitted draft and its unfiled evidence."""
    _voucher(conn, voucher_id)
    claim = conn.execute(
        "SELECT document_id, state, package_id, content_version FROM"
        " signout_expense_claims WHERE claim_id=? AND voucher_id=?",
        (claim_id, voucher_id)).fetchone()
    if not claim:
        raise ValidationFailure("not_found", "Receipt draft not found")
    if claim["state"] != "draft" or claim["package_id"]:
        raise ValidationFailure("claim_locked",
                                "Only receipts that have not been sent can be discarded")
    if claim["content_version"] != expected_content_version:
        from .repo import ConflictError
        raise ConflictError("Receipt draft changed; reload before discarding")
    conn.execute("DELETE FROM signout_claim_allocations WHERE claim_id=?",
                 (claim_id,))
    conn.execute("DELETE FROM signout_expense_claims WHERE claim_id=?",
                 (claim_id,))
    conn.execute("UPDATE documents SET state='discarded' WHERE document_id=?",
                 (claim["document_id"],))
    return {"claim_id": claim_id, "state": "discarded"}


def submit_package(conn, *, voucher_id, claim_ids) -> dict:
    """Selected drafts become one package (spec 12); one unresolved
    package per signout."""
    _voucher(conn, voucher_id)
    if _unresolved_package(conn, voucher_id):
        raise ValidationFailure("package_open",
                                "Another receipt submission is still open")
    if not claim_ids:
        raise ValidationFailure("empty_package", "Select at least one receipt")
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
        raise ValidationFailure(
            "claims_not_draft",
            "One or more selected receipts can no longer be sent; reload the page")
    return {"package_id": pid, "seq": seq}


def withdraw_package(conn, *, voucher_id, package_id) -> dict:
    p = _unresolved_package(conn, voucher_id)
    if not p or p["package_id"] != package_id:
        raise ValidationFailure("no_package", "Receipt submission not found")
    if p["status"] == "in_review":
        raise ValidationFailure("in_review", "This submission is currently being reviewed")
    conn.execute(
        "UPDATE documents SET state='signee_draft' WHERE document_id IN"
        " (SELECT document_id FROM signout_expense_claims WHERE"
        " package_id=?)", (package_id,))
    conn.execute(
        "UPDATE signout_expense_claims SET package_id=NULL,"
        " state='draft', verified_version=NULL WHERE package_id=?",
        (package_id,))
    conn.execute(
        "UPDATE signout_reconciliation_packages SET"
        " status='withdrawn', version=version+1,"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        " WHERE package_id=?", (package_id,))
    return {"package_id": package_id, "status": "withdrawn"}


def resubmit_package(conn, *, voucher_id, package_id) -> dict:
    """Return a corrected package to review without replacing its identity."""
    _voucher(conn, voucher_id)
    package = conn.execute(
        "SELECT status FROM signout_reconciliation_packages WHERE"
        " package_id=? AND voucher_id=?", (package_id, voucher_id)).fetchone()
    if not package or package["status"] != "needs_changes":
        raise ValidationFailure("not_returned",
                                "Only a returned submission can be sent again")
    claims = conn.execute(
        "SELECT claim_id, document_id, state FROM"
        " signout_expense_claims WHERE package_id=?", (package_id,)).fetchall()
    if not claims:
        raise ValidationFailure("empty_package", "This submission has no receipts")
    conn.execute(
        "UPDATE signout_expense_claims SET state='submitted' WHERE"
        " package_id=? AND state='flagged'", (package_id,))
    conn.execute(
        "UPDATE documents SET state='package_submitted' WHERE document_id IN"
        " (SELECT document_id FROM signout_expense_claims WHERE package_id=?)",
        (package_id,))
    conn.execute(
        "UPDATE signout_reconciliation_packages SET status='submitted',"
        " version=version+1, submitted_at="
        " strftime('%Y-%m-%dT%H:%M:%fZ','now'), updated_at="
        " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE package_id=?",
        (package_id,))
    return {"package_id": package_id, "status": "submitted",
            "receipts": len(claims)}


def set_claim_allocations(conn, *, claim_id, expected_content_version,
                          allocations) -> dict:
    """Replace the administrator's accounting allocation draft.

    These rows express accounting judgment, not a rewrite of the signee's
    submitted facts. They must nevertheless conserve both tender currencies
    and the receipt's face value before the item can be accepted.
    """
    claim = conn.execute(
        "SELECT c.*, p.status AS package_status FROM"
        " signout_expense_claims c JOIN"
        " signout_reconciliation_packages p ON p.package_id=c.package_id"
        " WHERE c.claim_id=?",
        (claim_id,)).fetchone()
    if not claim:
        raise ValidationFailure("not_found", "Submitted receipt not found")
    if claim["package_status"] not in ("submitted", "in_review"):
        raise ValidationFailure("package_not_reviewable",
                                "This submission is not available for review")
    if claim["content_version"] != expected_content_version:
        from .repo import ConflictError
        raise ConflictError(
            f"claim {claim_id} content_version "
            f"{claim['content_version']} != expected "
            f"{expected_content_version}")
    if not allocations:
        raise ValidationFailure("no_allocations",
                                "Choose at least one account code")

    expected_face_usd, expected_face_mxn = _receipt_faces(
        face_usd_cents=claim["face_usd_cents"],
        face_mxn_centavos=claim["face_mxn_centavos"])
    net_usd = claim["usd_paid_cents"] - claim["usd_change_cents"]
    net_mxn = (claim["mxn_paid_centavos"] -
               claim["mxn_change_centavos"])
    allocations = [dict(allocation) for allocation in allocations]
    if sum(int(a.get("usd_cents") or 0) for a in allocations) != net_usd or \
            sum(int(a.get("mxn_centavos") or 0) for a in allocations) != net_mxn:
        raise ValidationFailure(
            "allocation_mismatch",
            "The split cash amounts must add up to the cash used")
    if sum(int(a.get("face_usd_cents") or 0) for a in allocations) != expected_face_usd or \
            sum(int(a.get("face_mxn_centavos") or 0) for a in allocations) != expected_face_mxn:
        raise ValidationFailure(
            "face_allocation_mismatch",
            "The split receipt values must add up to the receipt value")

    for line, allocation in enumerate(allocations):
        account = conn.execute(
            "SELECT kind, active FROM accounts WHERE account_id=?",
            (allocation["expense_account"],)).fetchone()
        if not account or account["kind"] != "expense" or not account["active"]:
            raise ValidationFailure(
                "bad_expense_account", "Select an available account code")
        class_row = conn.execute(
            "SELECT active FROM classes WHERE class_code=?",
            (allocation.get("class_code"),)).fetchone()
        if not class_row or not class_row["active"]:
            raise ValidationFailure("bad_class", "Select an available class code")

    conn.execute("DELETE FROM signout_claim_allocations WHERE claim_id=?",
                 (claim_id,))
    for line, allocation in enumerate(allocations):
        conn.execute(
            "INSERT INTO signout_claim_allocations (claim_id, line_no,"
            " usd_cents, mxn_centavos, expense_account, class_code, project,"
            " description, notes, face_usd_cents, face_mxn_centavos)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (claim_id, line, allocation["usd_cents"], allocation["mxn_centavos"],
             allocation["expense_account"], allocation["class_code"],
             allocation.get("project", ""), allocation.get("description", ""),
             allocation.get("notes", ""),
             int(allocation.get("face_usd_cents") or 0) or None,
             int(allocation.get("face_mxn_centavos") or 0) or None))
    conn.execute(
        "UPDATE signout_expense_claims SET state='submitted',"
        " verified_version=NULL, updated_at="
        " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE claim_id=?",
        (claim_id,))
    conn.execute(
        "UPDATE signout_reconciliation_packages SET status='in_review',"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
        " WHERE package_id=? AND status='submitted'",
        (claim["package_id"],))
    return {"claim_id": claim_id, "allocations": len(allocations),
            "state": "submitted"}


def verify_claim(conn, *, claim_id, expected_content_version,
                 admin_note="") -> dict:
    """Admin marks a claim verified at its current content version.
    Only claims inside a submitted package can be verified (spec 11)."""
    c = conn.execute(
        "SELECT c.content_version, c.package_id, c.state,"
        " p.status AS package_status FROM signout_expense_claims c LEFT JOIN"
        " signout_reconciliation_packages p ON p.package_id=c.package_id"
        " WHERE c.claim_id = ?",
        (claim_id,)).fetchone()
    if not c:
        raise ValidationFailure("not_found", "Receipt not found")
    if not c["package_id"] or c["package_status"] not in (
            "submitted", "in_review"):
        raise ValidationFailure("not_submitted",
                                "Receipt has not been sent for review")
    if c["state"] not in ("submitted", "verified"):
        raise ValidationFailure("claim_not_reviewable",
                                "Receipt is not available to be checked")
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
    package = conn.execute(
        "SELECT status FROM signout_reconciliation_packages WHERE"
        " package_id=?", (package_id,)).fetchone()
    if not package or package["status"] not in ("submitted", "in_review"):
        raise ValidationFailure("not_reviewable",
                                "This submission is not available for corrections")
    if not str(admin_summary).strip():
        raise ValidationFailure("summary_required",
                                "Explain what the signee must correct")
    item_notes = {claim_id: str(note).strip()
                  for claim_id, note in (item_notes or {}).items()
                  if str(note).strip()}
    if not item_notes:
        raise ValidationFailure(
            "item_note_required",
            "Add a correction note to at least one receipt")
    claim_ids = {row[0] for row in conn.execute(
        "SELECT claim_id FROM signout_expense_claims WHERE package_id=?",
        (package_id,))}
    if not set(item_notes).issubset(claim_ids):
        raise ValidationFailure("bad_item_note",
                                "A correction note does not match this submission")
    for claim_id, note in item_notes.items():
        conn.execute(
            "UPDATE signout_expense_claims SET state='flagged',"
            " verified_version=NULL, admin_note=?, updated_at="
            " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE claim_id=?",
            (note, claim_id))
        conn.execute(
            "UPDATE documents SET state='signee_draft' WHERE document_id="
            " (SELECT document_id FROM signout_expense_claims WHERE"
            " claim_id=?)", (claim_id,))
    conn.execute(
        "UPDATE signout_reconciliation_packages SET"
        " status='needs_changes', admin_summary=?, version=version+1,"
        " reviewed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE"
        " package_id=?", (str(admin_summary).strip(), package_id))
    return {"package_id": package_id, "status": "needs_changes",
            "flagged": list(item_notes)}


def accept_package(conn, *, submission_id, package_id) -> dict:
    """Atomic acceptance (spec 11, 14): every claim verified at its
    current version; allocations summed to claim; all rows or none."""
    p = conn.execute(
        "SELECT * FROM signout_reconciliation_packages WHERE"
        " package_id=?", (package_id,)).fetchone()
    if not p or p["status"] not in ("submitted", "in_review"):
        raise ValidationFailure("not_open", "Receipt submission is not open")
    claims = conn.execute(
        "SELECT * FROM signout_expense_claims WHERE package_id=?",
        (package_id,)).fetchall()
    if not claims:
        raise ValidationFailure("empty", "This submission has no receipts")
    for c in claims:
        if c["state"] != "verified" or \
                c["verified_version"] != c["content_version"]:
            raise ValidationFailure(
                "unverified", "Every receipt must be marked as checked before approval")
    new_ids = []
    for c in claims:
        allocs = conn.execute(
            "SELECT * FROM signout_claim_allocations WHERE claim_id=?"
            " ORDER BY line_no", (c["claim_id"],)).fetchall()
        if not allocs:
            raise ValidationFailure(
                "no_allocations",
                "Each receipt needs an account code and class code")
        alloc_usd = sum(a["usd_cents"] for a in allocs)
        alloc_mxn = sum(a["mxn_centavos"] for a in allocs)
        net_usd = c["usd_paid_cents"] - c["usd_change_cents"]
        net_mxn = c["mxn_paid_centavos"] - c["mxn_change_centavos"]
        if alloc_usd != net_usd or alloc_mxn != net_mxn:
            raise ValidationFailure(
                "allocation_mismatch",
                "The split amounts must add up to the cash used")
        expected_face_usd, expected_face_mxn = _receipt_faces(
            face_usd_cents=c["face_usd_cents"],
            face_mxn_centavos=c["face_mxn_centavos"])
        allocated_face_usd = sum(a["face_usd_cents"] or 0 for a in allocs)
        allocated_face_mxn = sum(a["face_mxn_centavos"] or 0 for a in allocs)
        if allocated_face_usd != expected_face_usd or \
                allocated_face_mxn != expected_face_mxn:
            raise ValidationFailure(
                "face_allocation_mismatch",
                "The split receipt values must add up to the receipt value")
        for a in allocs:
            if not a["expense_account"] or not a["class_code"]:
                raise ValidationFailure(
                    "incomplete_allocation",
                    "Every split needs an account code and class code")
            account = conn.execute(
                "SELECT kind, active FROM accounts WHERE account_id=?",
                (a["expense_account"],)).fetchone()
            class_row = conn.execute(
                "SELECT active FROM classes WHERE class_code=?",
                (a["class_code"],)).fetchone()
            if not account or account["kind"] != "expense" or \
                    not account["active"] or not class_row or \
                    not class_row["active"]:
                raise ValidationFailure(
                    "inactive_allocation",
                    "A selected category is no longer available")
        cur = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM receipts WHERE"
            " voucher_id=?", (p["voucher_id"],)).fetchone()
        rid = f"{p['voucher_id']}-R{cur[0]}"
        conn.execute(
            "INSERT INTO receipts (receipt_id, voucher_id, seq,"
            " document_id, vendor,"
            " reviewed_date, face_usd_cents, face_mxn_centavos,"
            " usd_paid_cents, mxn_paid_centavos, usd_change_cents,"
            " mxn_change_centavos, payment_mode) VALUES (?,?,?,?,?,"
            " strftime('%Y-%m-%d','now'),?,?,?,?,?,?,?)",
            (rid, p["voucher_id"], cur[0], c["document_id"],
             c["vendor"],
             expected_face_usd or None, expected_face_mxn or None,
             c["usd_paid_cents"], c["mxn_paid_centavos"],
             c["usd_change_cents"], c["mxn_change_centavos"], c["payment_mode"]))
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
            rows.append(Row(
                row_id="pending", movement_id=mv,
                date=c["claim_date"], same_day_seq=seq,
                allocation_line=line, txn_type="expense",
                from_account=acct, to_account=a["expense_account"],
                usd=a["usd_cents"], mxn=a["mxn_centavos"],
                class_code=a["class_code"],
                receipt_value_mxn=a["face_mxn_centavos"],
                receipt_value_usd=a["face_usd_cents"],
                voucher_id=p["voucher_id"], receipt_id=rid))
        ids = repo.insert_rows(conn, submission_id, rows)
        for txn_id, allocation in zip(ids, allocs):
            conn.execute(
                "UPDATE transactions SET project=?, description=?, notes=?"
                " WHERE txn_id=?",
                (allocation["project"], allocation["description"],
                 allocation["notes"], txn_id))
            conn.execute(
                "INSERT INTO transaction_documents (txn_id, document_id,"
                " role) VALUES (?, ?, 'support')",
                (txn_id, c["document_id"]))
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
    derived = signouts.summary(conn, p["voucher_id"])
    completed = bool(derived and not derived["open"])
    return {"package_id": package_id, "accepted": new_ids,
            "signout_complete": completed}
