"""Validated in-place transaction history editing.

The ledger is intentionally correctable rather than append-only. Transaction
IDs remain stable, material changes require a reason, every edit records
before/after evidence, and run_command validates the projected full replay.
"""

from __future__ import annotations

import json

from . import repo
from .commands import ValidationFailure
from .model import OPENING_EQUITY, SAFE, UNALLOCATED_EXPENSE, amount_sign_errors


EDITABLE_FIELDS = (
    "date", "same_day_seq", "from_account", "to_account", "usd_cents",
    "mxn_centavos", "class_code", "receipt_face_usd_cents",
    "receipt_face_mxn_centavos", "assigned_rate_milli", "recipient", "vendor",
    "description", "project", "notes",
)
MATERIAL_FIELDS = {
    "date", "same_day_seq", "from_account", "to_account", "usd_cents",
    "mxn_centavos", "class_code", "receipt_face_usd_cents",
    "receipt_face_mxn_centavos", "assigned_rate_milli",
}


# Spec 14: edits are typed. Each kind of transaction exposes only the fields
# that describe it; accounts that follow from the workflow stay fixed.
_COMMON = {"date", "same_day_seq", "description", "notes"}
EDITABLE_BY_TYPE = {
    "expense": _COMMON | {"to_account", "class_code", "usd_cents", "mxn_centavos",
                          "receipt_face_usd_cents", "receipt_face_mxn_centavos",
                          "vendor", "project"},
    "signout": _COMMON | {"usd_cents", "mxn_centavos"},
    "addendum": _COMMON | {"usd_cents", "mxn_centavos"},
    "exchange": _COMMON | {"from_account", "usd_cents", "mxn_centavos"},
    "bank_withdrawal": _COMMON | {"usd_cents"},
    "opening": _COMMON | {"usd_cents", "mxn_centavos", "assigned_rate_milli"},
    "donation": _COMMON | {"usd_cents", "mxn_centavos", "assigned_rate_milli"},
    "adjustment": _COMMON | {"from_account", "to_account", "usd_cents",
                             "mxn_centavos", "assigned_rate_milli"},
}


def _snapshot(row):
    return {field: row[field] for field in (
        "txn_id", "movement_id", "date", "same_day_seq", "allocation_line",
        "txn_type", "from_account", "to_account", "usd_cents",
        "mxn_centavos", "voucher_id", "receipt_id", "addendum_id",
        "class_code",
        "receipt_face_usd_cents", "receipt_face_mxn_centavos",
        "assigned_rate_milli", "posting_state", "related_txn_id",
        "recipient", "vendor", "description", "project", "notes",
        "version", "void_reason", "voided_at")}


def edit_transaction(conn, *, submission_id, txn_id, expected_version,
                     changes, reason="") -> dict:
    current = conn.execute("SELECT * FROM transactions WHERE txn_id=?",
                           (txn_id,)).fetchone()
    if not current:
        raise ValidationFailure("not_found", "Transaction not found")
    repo.assert_versions(conn, "transactions", "txn_id",
                         [(txn_id, expected_version)])
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValidationFailure("unknown_fields",
                                f"Fields cannot be edited: {sorted(unknown)}")
    actual = {field: value for field, value in changes.items()
              if value != current[field]}
    if not actual:
        return {"txn_id": txn_id, "version": current["version"],
                "changed": []}
    fixed = sorted(set(actual) - EDITABLE_BY_TYPE.get(current["txn_type"], set(EDITABLE_FIELDS)))
    if fixed:
        raise ValidationFailure(
            "not_editable", f"These fields cannot change on this kind of transaction: {fixed}",
            {field: "fixed for this transaction type" for field in fixed})
    material = sorted(set(actual) & MATERIAL_FIELDS)
    if material and not reason.strip():
        raise ValidationFailure(
            "reason_required",
            "Explain the material history change before saving",
            {"reason": "required"})
    if current["posting_state"] == "active" and \
            current["txn_type"] == "expense" and \
            current["to_account"] != UNALLOCATED_EXPENSE:
        class_code = actual.get("class_code", current["class_code"])
        if not class_code:
            raise ValidationFailure(
                "class_required", "Completed expenses require a class code",
                {"class_code": "required"})
        if "class_code" in actual:
            class_row = conn.execute(
                "SELECT active FROM classes WHERE class_code=?",
                (class_code,)).fetchone()
            if not class_row or not class_row["active"]:
                raise ValidationFailure(
                    "bad_class", "Select an active class code",
                    {"class_code": "not available"})
    if current["txn_type"] == "opening":
        merged = {field: actual.get(field, current[field]) for field in (
            "from_account", "to_account", "usd_cents", "mxn_centavos",
            "assigned_rate_milli")}
        if merged["from_account"] != OPENING_EQUITY or \
                merged["to_account"] != SAFE:
            raise ValidationFailure(
                "bad_opening", "Starting cash must enter the physical safe")
        if merged["usd_cents"] < 0 or merged["mxn_centavos"] < 0 or not (
                merged["usd_cents"] or merged["mxn_centavos"]):
            raise ValidationFailure(
                "bad_opening", "Enter a positive starting cash amount")
        if merged["mxn_centavos"] and not merged["assigned_rate_milli"]:
            raise ValidationFailure(
                "rate_required", "Starting MXN needs the historical rate")
    sign_errors = amount_sign_errors(
        current["txn_type"], actual.get("usd_cents", current["usd_cents"]),
        actual.get("mxn_centavos", current["mxn_centavos"]))
    if sign_errors:
        if current["txn_type"] == "exchange":
            raise ValidationFailure(
                "bad_exchange", "Exchange needs positive USD paid and negative "
                "MXN received. Neither amount can be zero.", sign_errors)
        message = "Cash movement amounts cannot be negative."
        if current["txn_type"] in ("signout", "addendum"):
            message += (
                " To record cash returned, use a return addendum "
                "with a positive amount.")
        else:
            message += (
                " Use the source and destination accounts for cash direction.")
        raise ValidationFailure("negative_amount", message, sign_errors)
    for field in ("receipt_face_usd_cents", "receipt_face_mxn_centavos"):
        value = changes.get(field, current[field])
        if value is not None and value < 0:
            raise ValidationFailure("bad_amount",
                                    "Receipt values cannot be negative")

    before = _snapshot(current)
    assignments = ", ".join(f"{field}=?" for field in actual)
    conn.execute(
        f"UPDATE transactions SET {assignments}, version=version+1,"
        " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE txn_id=?",
        [*actual.values(), txn_id])
    updated = conn.execute("SELECT * FROM transactions WHERE txn_id=?",
                           (txn_id,)).fetchone()
    after = _snapshot(updated)
    conn.execute(
        "INSERT INTO transaction_edits (txn_id, submission_id, reason,"
        " before_json, after_json) VALUES (?, ?, ?, ?, ?)",
        (txn_id, submission_id, reason.strip(),
         json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True)))
    if material and current["voucher_id"]:
        conn.execute(
            "UPDATE vouchers SET status='pending', version=version+1,"
            " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE"
            " voucher_id=? AND type='signout' AND status='complete'",
            (current["voucher_id"],))
    return {"txn_id": txn_id, "version": updated["version"],
            "changed": sorted(actual), "material": material}


def void_transaction(conn, *, submission_id, txn_id, expected_version,
                     reason) -> dict:
    """Void a row, or a whole direct expense: its lines are one payment, so
    voiding any of them voids them all. A voucher left with no active rows
    is void too, with its receipt numbers and any unfinished review."""
    if not reason.strip():
        raise ValidationFailure("reason_required", "Void reason is required",
                                {"reason": "required"})
    current = conn.execute("SELECT * FROM transactions WHERE txn_id=?",
                           (txn_id,)).fetchone()
    if not current:
        raise ValidationFailure("not_found", "Transaction not found")
    if current["posting_state"] != "active":
        raise ValidationFailure("not_active", "Transaction is not active")
    repo.assert_versions(conn, "transactions", "txn_id",
                         [(txn_id, expected_version)])
    voucher = conn.execute(
        "SELECT voucher_id, type FROM vouchers WHERE voucher_id=?",
        (current["voucher_id"],)).fetchone() if current["voucher_id"] else None
    targets = conn.execute(
        "SELECT * FROM transactions WHERE voucher_id=? AND posting_state='active'"
        " ORDER BY txn_id", (voucher["voucher_id"],)).fetchall() \
        if voucher and voucher["type"] == "direct_expense" else [current]
    for row in targets:
        conn.execute(
            "UPDATE transactions SET posting_state='void', void_reason=?,"
            " voided_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
            " version=version+1, updated_at="
            " strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE txn_id=?",
            (reason.strip(), row["txn_id"]))
        updated = conn.execute("SELECT * FROM transactions WHERE txn_id=?",
                               (row["txn_id"],)).fetchone()
        conn.execute(
            "INSERT INTO transaction_edits (txn_id, submission_id, reason,"
            " before_json, after_json) VALUES (?, ?, ?, ?, ?)",
            (row["txn_id"], submission_id, reason.strip(),
             json.dumps(_snapshot(row), sort_keys=True),
             json.dumps(_snapshot(updated), sort_keys=True)))
    voucher_void = bool(voucher) and not conn.execute(
        "SELECT 1 FROM transactions WHERE voucher_id=? AND posting_state='active'"
        " LIMIT 1", (voucher["voucher_id"],)).fetchone()
    if voucher_void:
        conn.execute(
            "UPDATE vouchers SET status='void', void_reason=?, version=version+1,"
            " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE voucher_id=?",
            (reason.strip(), voucher["voucher_id"]))
        conn.execute(
            "UPDATE receipts SET status='void', version=version+1,"
            " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"
            " WHERE voucher_id=? AND status!='void'", (voucher["voucher_id"],))
        conn.execute("DELETE FROM direct_expense_review_drafts WHERE voucher_id=?",
                     (voucher["voucher_id"],))
    version = conn.execute("SELECT version FROM transactions WHERE txn_id=?",
                           (txn_id,)).fetchone()[0]
    return {"txn_id": txn_id, "version": version, "posting_state": "void",
            "voided_txn_ids": [row["txn_id"] for row in targets],
            "voucher_void": voucher_void}
