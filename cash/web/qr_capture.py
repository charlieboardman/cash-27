"""Capture recognition and orientation, separate from approval and filing."""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys

from .. import document_codes
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
                capture_output=True, text=True, timeout=config["QR_TIMEOUT"],
                pass_fds=(config["WORKER_LOCK_FD"],) if "WORKER_LOCK_FD" in config else ())
            if result.returncode:
                return {"status": "failed", "ids": []}
            payload = json.loads(result.stdout)
            if not isinstance(payload.get("ids"), list):
                raise ValueError("Invalid decoder output")
            ids = valid_ids(payload["ids"])
            rotations = payload.get("rotations", [])
            if not isinstance(rotations, list) or len(rotations) > 40 or any(
                    angle is not None and (type(angle) is not int or angle not in (0, 90, 180, 270))
                    for angle in rotations):
                raise ValueError("Invalid page orientations")
            qr_pages = payload.get("qr_pages", [])
            if not isinstance(qr_pages, list) or (qr_pages and (
                    len(qr_pages) != len(rotations) or any(type(value) is not bool for value in qr_pages))):
                raise ValueError("Invalid QR page markers")
            page_ids = payload.get("page_ids", [])
            if not isinstance(page_ids, list) or (page_ids and (
                    len(page_ids) != len(rotations) or not all(isinstance(value, list) for value in page_ids))):
                raise ValueError("Invalid page codes")
            return {"status": ("detected" if len(ids) == 1 else
                               "ambiguous" if ids else "no_code"),
                    "ids": ids, "rotations": rotations, "qr_pages": qr_pages,
                    "page_ids": [valid_ids(value) for value in page_ids]}
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
        QR_TIMEOUT=120,
        RECEIPT_ORIENTATION_ENABLED=os.environ.get("CASH_RECEIPT_ORIENTATION_ENABLED", "0") == "1",
        RECEIPT_ORIENTATION_WEIGHTS=os.environ.get(
            "CASH_RECEIPT_ORIENTATION_WEIGHTS", "/opt/cash27-orientation/inference.onnx"),
        RECEIPT_ORIENTATION_TIMEOUT=60,
    )


def receipt_rotations(path, recognition, config):
    """Run after QReader exits; never override or guess around a voucher QR."""
    rotations = recognition.get("rotations", [])
    qr_pages = recognition.get("qr_pages", [])
    if not config.get("RECEIPT_ORIENTATION_ENABLED") or not qr_pages:
        return recognition
    pages = [i for i, has_qr in enumerate(qr_pages) if not has_qr]
    if not pages or len(qr_pages) != len(rotations):
        return recognition
    with (config["DATA_ROOT"] / "staging" / "qr.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return recognition
        try:
            result = subprocess.run(
                [config["QR_PYTHON"], "-m", "cash.receipt_orientation_worker", str(path),
                 "--weights", config["RECEIPT_ORIENTATION_WEIGHTS"], "--pages", json.dumps(pages)],
                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
                timeout=config["RECEIPT_ORIENTATION_TIMEOUT"],
                pass_fds=(config["WORKER_LOCK_FD"],) if "WORKER_LOCK_FD" in config else ())
            if result.returncode:
                return recognition
            proposed = json.loads(result.stdout)["rotations"]
            if not isinstance(proposed, list) or len(proposed) != len(rotations) or any(
                    value is not None and (type(value) is not int or value not in (0, 90, 180, 270))
                    for value in proposed):
                return recognition
            merged = list(rotations)
            for index in pages:
                merged[index] = proposed[index]
            return {**recognition, "rotations": merged}
        except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
            return recognition


def page_groups(page_ids):
    """Split one scan into documents by the code on each page.

    A page whose code differs from the page before starts a new document, so
    several papers scanned as one stack come apart, while consecutive sheets
    with the same code (a purchase log) stay together. A page without a code,
    or with more than one, stays with the page before it; pages before the
    first code are a document of their own. Returns [(page indexes, code)]."""
    groups = []
    for index, ids in enumerate(page_ids):
        code = ids[0] if len(ids) == 1 else None
        if not groups or (code and code != groups[-1][1]):
            groups.append(([], code))
        groups[-1][0].append(index)
    return groups


def split_pdf(contents, groups):
    """One PDF per group of page indexes, pages kept exactly as they are."""
    import io
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(contents))
    pieces = []
    for pages in groups:
        writer = PdfWriter()
        for index in pages:
            writer.add_page(reader.pages[index])
        output = io.BytesIO()
        writer.write(output)
        pieces.append(output.getvalue())
    return pieces


def capture_name(recognition, captured_at):
    if recognition["status"] == "detected":
        return recognition["ids"][0] + ".pdf"
    return captured_at.strftime("scan-%Y%m%d-%H%M%S-%fZ.pdf")


def orient_pdf(contents, recognition):
    """Rotate before immutable publication; preserve image streams and quality."""
    import io
    from pypdf import PdfReader, PdfWriter

    rotations = recognition.get("rotations", [])
    if not any(rotations):
        return contents
    reader = PdfReader(io.BytesIO(contents))
    if len(rotations) != len(reader.pages):
        raise ValueError("Orientation count does not match PDF pages")
    writer = PdfWriter()
    for page, angle in zip(reader.pages, rotations):
        if angle:
            page.rotate(angle)
        writer.add_page(page)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def record_match(conn, document_id, recognition):
    """Called inside the capture transaction, so simultaneous matches cannot win."""
    status = recognition["status"]
    detected = recognition["ids"][0] if status == "detected" else None
    code = document_codes.parse(detected)
    voucher_id = addendum_id = kind = target = None
    if detected:
        if code and code.kind == "addendum":
            target = conn.execute(
                "SELECT a.voucher_id, a.status, v.status AS voucher_status"
                " FROM addenda a JOIN vouchers v USING(voucher_id)"
                " WHERE a.addendum_id=?", (detected,)).fetchone()
            kind = "addendum"
        elif code and code.kind == "voucher":
            target = conn.execute(
                "SELECT voucher_id, status, status AS voucher_status, type"
                " FROM vouchers WHERE voucher_id=?", (detected,)).fetchone()
            kind = target["type"] if target else None
        status = "unknown"
        if code and code.kind == "count":
            status = _count_match(conn, code)
            kind = "count" if status == "matched" else None
        elif code and code.kind == "statement":
            status, voucher_id = _statement_match(conn, code)
            kind = "statement" if status == "matched" else None
        elif code and code.kind == "log":
            status, voucher_id = _log_match(conn, code)
            kind = "log" if status == "matched" else None
        elif target:
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
            "review_url": _review_url(status, kind, detected, voucher_id)}


def _review_url(status, kind, detected, voucher_id):
    if status != "matched":
        return None
    # Statements and purchase logs are attached on the expense report.
    if kind in ("statement", "log"):
        return f"/{voucher_id}"
    if kind == "count":
        return f"/review?item=count:{document_codes.parse(detected).txn_id}"
    return f"/review?item={kind}:{detected}"


def _count_match(conn, code):
    """A safe count sheet belongs to the count it names, until its sheet is filed."""
    from ..prep import safe_count_rows
    rows = safe_count_rows(conn, code.txn_id)
    if not rows or rows[0]["txn_id"] != code.txn_id:
        return "unknown"
    if rows[0]["posting_state"] != "active":
        return "inactive"
    if conn.execute(
            "SELECT 1 FROM transaction_documents WHERE role='support' AND txn_id IN"
            f" ({','.join('?' for _ in rows)})", [row["txn_id"] for row in rows]).fetchone():
        return "already_filed"
    if conn.execute(
            "SELECT 1 FROM document_qr q JOIN documents d USING(document_id)"
            " WHERE q.detected_id=? AND q.status='matched' AND d.state='inbox'",
            (code.text,)).fetchone():
        return "duplicate"
    return "matched"


def _log_match(conn, code):
    """(status, voucher_id) for purchase log sheets: they belong to the
    signout's expense report, whether or not its log row exists yet."""
    voucher = conn.execute("SELECT status, type FROM vouchers WHERE voucher_id=?",
                           (code.voucher_id,)).fetchone()
    if not voucher or voucher["type"] != "signout":
        return "unknown", None
    if voucher["status"] == "void":
        return "inactive", None
    if conn.execute(
            "SELECT 1 FROM document_qr q JOIN documents d USING(document_id)"
            " WHERE q.detected_id=? AND q.status='matched' AND d.state='inbox'",
            (code.text,)).fetchone():
        return "duplicate", None
    return "matched", code.voucher_id


def _statement_match(conn, code):
    """(status, voucher_id) for a signed no-receipt statement: it belongs to
    the expense report row holding that receipt number."""
    from .. import expense_drafts
    voucher = conn.execute("SELECT status, type FROM vouchers WHERE voucher_id=?",
                           (code.voucher_id,)).fetchone()
    found = expense_drafts.stored(conn, code.voucher_id) if voucher else None
    if not voucher or voucher["type"] != "signout" or not found:
        return "unknown", None
    receipt_id = f"{code.voucher_id}-R{code.receipt_seq}"
    row = next((row for row in found[0]["rows"] if row.get("receipt_number") == code.receipt_seq
                or (row["posted"] or {}).get("receipt_id") == receipt_id), None)
    if not row:
        return "unknown", None
    if voucher["status"] == "void":
        return "inactive", None
    if row["document_id"]:
        return "already_filed", None
    if conn.execute(
            "SELECT 1 FROM document_qr q JOIN documents d USING(document_id)"
            " WHERE q.detected_id=? AND q.status='matched' AND d.state='inbox'",
            (code.text,)).fetchone():
        return "duplicate", None
    return "matched", code.voucher_id


def metadata(conn, document_id):
    row = conn.execute("SELECT * FROM document_qr WHERE document_id=?",
                       (document_id,)).fetchone()
    if not row:
        return None
    return {"status": row["status"], "message": STATUS_LABELS[row["status"]],
            "detected_id": row["detected_id"],
            "voucher_id": row["voucher_id"], "addendum_id": row["addendum_id"],
            "review_url": _review_url(row["status"], row["review_kind"],
                                      row["detected_id"], row["voucher_id"])}
