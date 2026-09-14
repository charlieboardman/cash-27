"""Capture immutable PDFs and create their logical inbox records."""

from __future__ import annotations

from datetime import datetime, timezone

import hashlib
from pathlib import Path
import tempfile

from flask import (Blueprint, current_app, g, request,
                   send_file)

from .app import ok, err, require_admin
from .documents import (LIMITS, normalize_document,
                        normalize_document_parts, upload_display_name,
                        document_rel_path, reserve_document_ids, _publish_pdf)
from . import qr_capture

bp = Blueprint("capture", __name__)


def _prepare(files, document_id, captured_at, position=None, *, merge=False):
    """Normalize in staging, then publish once with the final recognized name."""
    root = current_app.config["DATA_ROOT"]
    display_name = upload_display_name(files[0].filename, len(files))
    rel_path = document_rel_path(document_id, display_name, captured_at, position)
    normalizer = normalize_document_parts if merge else normalize_document
    streams = [uploaded.stream for uploaded in files] if merge else files[0].stream
    if not current_app.config["QR_ENABLED"]:
        meta = normalizer(streams, root / "documents", rel_path)
        return rel_path, meta, display_name, None
    with tempfile.TemporaryDirectory(prefix="qr-", dir=root / "staging") as stage:
        meta = normalizer(streams, Path(stage) / "documents", rel_path)
        try:
            recognition = qr_capture.recognize(meta["path"], current_app.config)
        except Exception:
            current_app.logger.exception("QR decoding failed; retaining uploaded document")
            recognition = {"status": "failed", "ids": []}
        # Load output bytes only after the model process has exited.
        contents = Path(meta["path"]).read_bytes()
        try:
            contents = qr_capture.orient_pdf(contents, recognition)
        except Exception:
            current_app.logger.exception("QR rotation failed; retaining uploaded document")
            recognition = {"status": "failed", "ids": []}
        display_name = qr_capture.capture_name(recognition, captured_at)
        rel_path = document_rel_path(document_id, display_name, captured_at, position)
        target = _publish_pdf(contents, root / "documents", rel_path)
        meta["path"] = str(target)
        meta["sha256"] = hashlib.sha256(contents).hexdigest()
        meta["size"] = len(contents)
    return rel_path, meta, display_name, recognition


@bp.post("/api/documents/capture")
def capture():
    guard = require_admin()
    if guard:
        return guard
    files = request.files.getlist("files")
    if not files:
        return err("no_files", "No files", 422)
    if len(files) > LIMITS["documents_per_request"]:
        return err("too_many_documents", "Document limit", 422)
    captured_at = datetime.now(timezone.utc)
    document_ids = reserve_document_ids(g.db, len(files))
    created = []
    published = []
    records = []
    try:
        for position, (f, document_id) in enumerate(
                zip(files, document_ids), start=1):
            rel_path, meta, display_name, recognition = _prepare(
                [f], document_id, captured_at,
                position if len(files) > 1 else None)
            published.append(current_app.config["DATA_ROOT"] / rel_path)
            records.append((document_id, rel_path, meta, display_name, recognition))
            created.append({"document_id": document_id,
                            "display_name": display_name,
                            "sha256": meta["sha256"],
                            "size": meta["size"], "pages": meta["pages"]})
        g.db.execute("BEGIN IMMEDIATE")
        for item, record in zip(created, records):
            document_id, rel_path, meta, display_name, recognition = record
            g.db.execute(
                "INSERT INTO documents (document_id, rel_path, sha256,"
                " size_bytes, page_count, display_name, state)"
                " VALUES (?,?,?,?,?,?, 'inbox')",
                (document_id, rel_path, meta["sha256"],
                 meta["size"], meta["pages"], display_name))
            if recognition:
                item["qr"] = qr_capture.record_match(g.db, document_id, recognition)
        g.db.execute("COMMIT")
    except Exception:
        if g.db.in_transaction:
            g.db.execute("ROLLBACK")
        for path in published:
            path.unlink(missing_ok=True)
        raise
    return ok({"documents": created})


@bp.post("/api/documents/capture-merge")
def capture_merge():
    """One capture card from several files in order (spec 9)."""
    guard = require_admin()
    if guard:
        return guard
    files = request.files.getlist("files")
    if not files:
        return err("no_files", "No files", 422)
    captured_at = datetime.now(timezone.utc)
    document_id = reserve_document_ids(g.db, 1)[0]
    published = False
    match = None
    try:
        rel_path, meta, display_name, recognition = _prepare(
            files, document_id, captured_at, merge=True)
        target = current_app.config["DATA_ROOT"] / rel_path
        published = True
        g.db.execute("BEGIN IMMEDIATE")
        g.db.execute(
            "INSERT INTO documents (document_id, rel_path, sha256,"
            " size_bytes, page_count, display_name, state)"
            " VALUES (?,?,?,?,?,?, 'inbox')",
            (document_id, rel_path, meta["sha256"],
             meta["size"], meta["pages"], display_name))
        if recognition:
            match = qr_capture.record_match(g.db, document_id, recognition)
        g.db.execute("COMMIT")
    except Exception:
        if g.db.in_transaction:
            g.db.execute("ROLLBACK")
        if published:
            target.unlink(missing_ok=True)
        raise
    return ok({"document_id": document_id, "pages": meta["pages"],
               "display_name": display_name, "qr": match})


@bp.get("/api/documents/inbox")
def inbox():
    guard = require_admin()
    if guard:
        return guard
    rows = g.db.execute(
        "SELECT document_id, sha256, size_bytes, page_count, state,"
        " display_name, created_at FROM documents WHERE state='inbox'"
        " ORDER BY created_at").fetchall()
    return ok({"documents": [dict(r) | {"qr": qr_capture.metadata(g.db, r["document_id"])}
                             for r in rows]})


@bp.get("/api/documents/<document_id>/stream")
def stream(document_id):
    guard = require_admin()
    if guard:
        return guard
    row = g.db.execute(
        "SELECT rel_path, sha256, display_name FROM documents WHERE document_id = ?",
        (document_id,)).fetchone()
    if not row:
        return err("not_found", "Document not found", 404)
    path = current_app.config["DATA_ROOT"] / row["rel_path"]
    if not path.exists():
        return err("missing", "File missing", 410)
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        return err("hash_mismatch", "File corrupted", 410)
    return send_file(path, mimetype="application/pdf",
                     download_name=(row["display_name"]
                                    if row["display_name"].lower().endswith(".pdf")
                                    else path.name))


@bp.post("/api/documents/<document_id>/discard")
def discard(document_id):
    guard = require_admin()
    if guard:
        return guard
    row = g.db.execute(
        "SELECT state, rel_path FROM documents WHERE document_id = ?",
        (document_id,)).fetchone()
    if not row or row["state"] != "inbox":
        return err("not_in_inbox", "Document not in inbox", 409)
    g.db.execute("UPDATE documents SET state='discarded' WHERE"
                 " document_id = ?", (document_id,))
    g.db.commit()
    return ok({"document_id": document_id})
