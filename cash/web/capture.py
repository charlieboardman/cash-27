"""Capture immutable PDFs and create their logical inbox records."""

from __future__ import annotations

import uuid
from pathlib import Path

import hashlib

from flask import (Blueprint, current_app, g, request,
                   send_file)

from ..commands import ValidationFailure
from .app import ok, err, require_admin
from .documents import (LIMITS, normalize_document,
                        normalize_document_parts, upload_display_name,
                        document_rel_path)

bp = Blueprint("capture", __name__)


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
    document_store = current_app.config["DATA_ROOT"] / "documents"
    created = []
    for f in files:
        document_id = str(uuid.uuid4())
        meta = normalize_document(f.stream, document_store, document_id)
        display_name = upload_display_name(f.filename)
        try:
            g.db.execute(
                "INSERT INTO documents (document_id, rel_path, sha256,"
                " size_bytes, page_count, display_name, state)"
                " VALUES (?,?,?,?,?,?, 'inbox')",
                (document_id, document_rel_path(document_id), meta["sha256"],
                 meta["size"], meta["pages"], display_name))
        except Exception:
            Path(meta["path"]).unlink(missing_ok=True)
            raise
        created.append({"document_id": document_id,
                        "display_name": display_name,
                        "sha256": meta["sha256"],
                        "size": meta["size"], "pages": meta["pages"]})
    g.db.commit()
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
    document_id = str(uuid.uuid4())
    document_store = current_app.config["DATA_ROOT"] / "documents"
    meta = normalize_document_parts(
        [uploaded.stream for uploaded in files], document_store, document_id)
    display_name = upload_display_name(files[0].filename, len(files))
    try:
        g.db.execute(
            "INSERT INTO documents (document_id, rel_path, sha256,"
            " size_bytes, page_count, display_name, state)"
            " VALUES (?,?,?,?,?,?, 'inbox')",
            (document_id, document_rel_path(document_id), meta["sha256"],
             meta["size"], meta["pages"], display_name))
    except Exception:
        Path(meta["path"]).unlink(missing_ok=True)
        raise
    g.db.commit()
    return ok({"document_id": document_id, "pages": meta["pages"],
               "display_name": display_name})


@bp.get("/api/documents/inbox")
def inbox():
    guard = require_admin()
    if guard:
        return guard
    rows = g.db.execute(
        "SELECT document_id, sha256, size_bytes, page_count, state,"
        " display_name, created_at FROM documents WHERE state='inbox'"
        " ORDER BY created_at").fetchall()
    return ok({"documents": [dict(r) for r in rows]})


@bp.get("/api/documents/<document_id>/stream")
def stream(document_id):
    guard = require_admin()
    if guard:
        return guard
    row = g.db.execute(
        "SELECT rel_path, sha256 FROM documents WHERE document_id = ?",
        (document_id,)).fetchone()
    if not row:
        return err("not_found", "Document not found", 404)
    path = current_app.config["DATA_ROOT"] / row["rel_path"]
    if not path.exists():
        return err("missing", "File missing", 410)
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        return err("hash_mismatch", "File corrupted", 410)
    return send_file(path, mimetype="application/pdf")


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
