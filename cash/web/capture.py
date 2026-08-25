"""Capture API: upload documents to the inbox (spec 9, 13). Each
request may carry several files; each becomes one independent document.
Multipart pages of one document are merged in order into a real PDF."""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import hashlib

from flask import (Blueprint, current_app, g, request,
                   send_file)

from ..commands import ValidationFailure
from .app import ok, err, require_admin
from .documents import LIMITS, normalize_document

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
    inbox = current_app.config["DATA_ROOT"] / "inbox"
    created = []
    for f in files:
        document_id = str(uuid.uuid4())
        meta = normalize_document(f.stream, inbox, document_id)
        g.db.execute(
            "INSERT INTO documents (document_id, rel_path, sha256,"
            " size_bytes, page_count, state) VALUES (?,?,?,?,?, 'inbox')",
            (document_id, str(Path(meta["path"]).relative_to(
                current_app.config["DATA_ROOT"])), meta["sha256"],
             meta["size"], meta["pages"]))
        created.append({"document_id": document_id, **meta})
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
    from .documents import merge_to_pdf, LIMITS
    items = []
    total = 0
    for f in files:
        data = f.stream.read()
        total += len(data)
        if total > LIMITS["request_bytes"]:
            return err("request_too_large", "Request limit", 422)
        mime = sniff_type(data[:8])
        if mime is None:
            return err("unsupported_type", "PDF, JPEG, or PNG only", 422)
        items.append((data, mime))
    merged = merge_to_pdf(items)
    document_id = str(uuid.uuid4())
    inbox = current_app.config["DATA_ROOT"] / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{document_id}.pdf"
    path.write_bytes(merged)
    from pypdf import PdfReader
    import io as _io, hashlib as _hashlib
    pages = len(PdfReader(_io.BytesIO(merged)).pages)
    if pages > LIMITS["pages_per_document"]:
        path.unlink()
        return err("too_many_pages", "Page limit", 422)
    digest = _hashlib.sha256(merged).hexdigest()
    g.db.execute(
        "INSERT INTO documents (document_id, rel_path, sha256,"
        " size_bytes, page_count, state) VALUES (?,?,?,?,?, 'inbox')",
        (document_id, f"inbox/{document_id}.pdf", digest,
         len(merged), pages))
    g.db.commit()
    return ok({"document_id": document_id, "pages": pages})


@bp.get("/api/documents/inbox")
def inbox():
    guard = require_admin()
    if guard:
        return guard
    rows = g.db.execute(
        "SELECT document_id, sha256, size_bytes, page_count, state,"
        " created_at FROM documents WHERE state='inbox'"
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
