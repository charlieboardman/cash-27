"""Optional QR capture experiment: recognition is not approval or filing."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from ..qr_worker import valid_ids

STATUS_LABELS = {
    "matched": "Matched; check the document and signatures before filing",
    "no_code": "No voucher QR found; choose a record manually",
    "ambiguous": "Multiple voucher IDs found; review manually",
    "unknown": "Voucher ID not found; review manually",
    "already_filed": "This paper is already filed; review the additional scan",
    "duplicate": "Another scan is awaiting review for this paper",
    "inactive": "This record is no longer active; review manually",
    "failed": "QR could not be read; document saved for manual review",
    "busy": "QR reader was busy; document saved for manual review",
    "timeout": "QR reading timed out; document saved for manual review",
}


def recognize(path, config):
    """Serialize short-lived decoders; failure always leaves a usable upload."""
    root = config["DATA_ROOT"]
    with (root / "staging" / "qr.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy", "ids": []}
        try:
            result = subprocess.run(
                [config["QR_PYTHON"], "-m", "cash.qr_worker", str(path),
                 "--weights", config["QR_WEIGHTS"], "--model", config["QR_MODEL"]],
                cwd=Path(__file__).resolve().parents[2],
                env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
                capture_output=True, text=True, timeout=config["QR_TIMEOUT"])
            if result.returncode:
                return {"status": "failed", "ids": []}
            payload = json.loads(result.stdout)
            if not isinstance(payload.get("ids"), list):
                raise ValueError("Invalid decoder output")
            ids = valid_ids(payload["ids"])
            return {"status": ("detected" if len(ids) == 1 else
                               "ambiguous" if ids else "no_code"), "ids": ids}
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "ids": []}
        except (OSError, ValueError, TypeError, AttributeError):
            return {"status": "failed", "ids": []}


def configure(app):
    app.config.update(
        QR_ENABLED=os.environ.get("CASH_QR_ENABLED", "0") == "1",
        QR_PYTHON=os.environ.get("CASH_QR_PYTHON", sys.executable),
        QR_WEIGHTS=os.environ.get("CASH_QR_WEIGHTS", "/opt/cash27-qr-weights"),
        QR_MODEL=os.environ.get("CASH_QR_MODEL", "n"),
        QR_TIMEOUT=45,
    )


def capture_name(recognition, captured_at):
    if recognition["status"] == "detected":
        return recognition["ids"][0] + ".pdf"
    return captured_at.strftime("scan-%Y%m%d-%H%M%S-%fZ.pdf")


def record_match(conn, document_id, recognition):
    """Called inside the capture transaction, so simultaneous matches cannot win."""
    status = recognition["status"]
    detected = recognition["ids"][0] if status == "detected" else None
    voucher_id = addendum_id = kind = None
    if detected:
        if "-A" in detected:
            target = conn.execute(
                "SELECT a.voucher_id, a.status, v.status AS voucher_status"
                " FROM addenda a JOIN vouchers v USING(voucher_id)"
                " WHERE a.addendum_id=?", (detected,)).fetchone()
            kind = "addendum"
        else:
            target = conn.execute(
                "SELECT voucher_id, status, status AS voucher_status, type"
                " FROM vouchers WHERE voucher_id=?", (detected,)).fetchone()
            kind = target["type"] if target else None
        status = "unknown"
        if target:
            condition = ("t.addendum_id=?" if kind == "addendum" else
                         "t.voucher_id=? AND t.addendum_id IS NULL"
                         " AND t.txn_type IN ('expense','signout')")
            if target["voucher_status"] == "void" or target["status"] == "void":
                status = "inactive"
            elif target["status"] == "complete" or conn.execute(
                    "SELECT 1 FROM transaction_documents td JOIN transactions t"
                    " USING(txn_id) WHERE td.role='voucher' AND"
                    " t.posting_state='active' AND " + condition,
                    (detected,)).fetchone():
                status = "already_filed"
            elif not conn.execute(
                    "SELECT 1 FROM transactions t WHERE"
                    " t.posting_state='active' AND " + condition,
                    (detected,)).fetchone():
                status = "inactive"
            elif conn.execute(
                    "SELECT 1 FROM document_qr q JOIN documents d USING(document_id)"
                    " WHERE q.detected_id=? AND q.status='matched' AND d.state='inbox'",
                    (detected,)).fetchone():
                status = "duplicate"
            else:
                status = "matched"
                voucher_id = target["voucher_id"]
                addendum_id = detected if kind == "addendum" else None
    conn.execute(
        "INSERT INTO document_qr (document_id, status, detected_id, decoded_ids,"
        " voucher_id, addendum_id, review_kind) VALUES (?,?,?,?,?,?,?)",
        (document_id, status, detected, json.dumps(recognition["ids"]),
         voucher_id, addendum_id, kind if status == "matched" else None))
    return {"status": status, "message": STATUS_LABELS[status],
            "detected_id": detected, "voucher_id": voucher_id,
            "addendum_id": addendum_id,
            "review_url": f"/review?item={kind}:{detected}" if status == "matched" else None}


def metadata(conn, document_id):
    row = conn.execute("SELECT * FROM document_qr WHERE document_id=?",
                       (document_id,)).fetchone()
    if not row:
        return None
    return {"status": row["status"], "message": STATUS_LABELS[row["status"]],
            "detected_id": row["detected_id"],
            "voucher_id": row["voucher_id"], "addendum_id": row["addendum_id"],
            "review_url": (f"/review?item={row['review_kind']}:{row['detected_id']}"
                           if row["status"] == "matched" else None)}
