"""Capture immutable PDFs and create their logical inbox records."""

from __future__ import annotations

from datetime import datetime, timezone

import hashlib
import json
import uuid

from flask import (Blueprint, abort, current_app, g, render_template, request,
                   send_file, url_for)

from .. import document_jobs
from ..commands import ValidationFailure
from .app import admin_required, err, ok
from .documents import (LIMITS, normalize_document_parts, upload_display_name,
                        document_rel_path, reserve_document_ids,
                        document_thumbnail, store_document)
from . import qr_capture

bp = Blueprint("capture", __name__)


def _enqueue(document_id, rel_path, meta, captured_at):
    """Models run only in the worker."""
    if current_app.config["QR_ENABLED"]:
        document_jobs.enqueue(g.db, document_id, rel_path, meta["sha256"], captured_at)


def _capture_retry(upload_id, source_hash):
    row = g.db.execute("SELECT command_kind, state, response_json FROM submissions WHERE submission_id=?",
                       (upload_id,)).fetchone()
    if row is None:
        return None
    if row["command_kind"] != "capture_upload" or row["state"] != "committed":
        raise ValidationFailure("upload_id_conflict", "Upload ID is already in use")
    saved = json.loads(row["response_json"])
    if saved["source_sha256"] != source_hash:
        raise ValidationFailure("upload_id_conflict", "Upload ID belongs to different file contents")
    return saved["result"]


@bp.post("/api/documents/capture")
@admin_required
def capture():
    files = request.files.getlist("files")
    if not files:
        return err("no_files", "No files", 422)
    if len(files) > LIMITS["documents_per_request"]:
        return err("too_many_documents", "Document limit", 422)
    upload_id = request.headers.get("X-Upload-ID")
    source_hash = None
    if upload_id:
        try:
            upload_id = str(uuid.UUID(upload_id))
        except ValueError:
            return err("bad_upload_id", "Upload ID must be a UUID", 422)
        fingerprints = []
        for uploaded in files:
            digest = hashlib.sha256()
            size = 0
            while block := uploaded.stream.read(1024 * 1024):
                size += len(block)
                if size > LIMITS["file_bytes"]:
                    return err("file_too_large", "File exceeds capture limit", 422)
                digest.update(block)
            uploaded.stream.seek(0)
            fingerprints.append((uploaded.filename, digest.hexdigest()))
        source_hash = hashlib.sha256(json.dumps(fingerprints).encode()).hexdigest()
        previous = _capture_retry(upload_id, source_hash)
        if previous is not None:
            return ok(previous)
    captured_at = datetime.now(timezone.utc)
    document_ids = reserve_document_ids(g.db, len(files))
    created = []
    published = []
    records = []
    try:
        for position, (f, document_id) in enumerate(
                zip(files, document_ids), start=1):
            display_name = upload_display_name(f.filename)
            rel_path = document_rel_path(document_id, display_name, captured_at,
                                         position if len(files) > 1 else None)
            meta = normalize_document_parts([f.stream], current_app.config["DATA_ROOT"] / "documents",
                                            rel_path)
            published.append(current_app.config["DATA_ROOT"] / rel_path)
            records.append((document_id, rel_path, meta, display_name))
            created.append({"document_id": document_id,
                            "display_name": display_name,
                            "sha256": meta["sha256"],
                            "size": meta["size"], "pages": meta["pages"]})
        g.db.execute("BEGIN IMMEDIATE")
        if upload_id:
            previous = _capture_retry(upload_id, source_hash)
            if previous is not None:
                g.db.execute("ROLLBACK")
                for path in published:
                    path.unlink(missing_ok=True)
                return ok(previous)
        for item, record in zip(created, records):
            document_id, rel_path, meta, display_name = record
            g.db.execute(
                "INSERT INTO documents (document_id, rel_path, sha256,"
                " size_bytes, page_count, display_name, state)"
                " VALUES (?,?,?,?,?,?, 'inbox')",
                (document_id, rel_path, meta["sha256"],
                 meta["size"], meta["pages"], display_name))
            _enqueue(document_id, rel_path, meta, captured_at)
            item["processing"] = document_jobs.metadata(g.db, document_id)
            item["qr"] = None
        result = {"documents": created}
        if upload_id:
            result["upload_id"] = upload_id
            g.db.execute("INSERT INTO submissions (submission_id, command_kind, state, response_json, committed_at)"
                         " VALUES (?, 'capture_upload', 'committed', ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                         (upload_id, json.dumps({"source_sha256": source_hash, "result": result})))
        g.db.execute("COMMIT")
    except Exception:
        if g.db.in_transaction:
            g.db.execute("ROLLBACK")
        for path in published:
            path.unlink(missing_ok=True)
        raise
    return ok(result)


@bp.post("/api/documents/capture-merge")
@admin_required
def capture_merge():
    """One capture card from several files in order (spec 9)."""
    files = request.files.getlist("files")
    if not files:
        return err("no_files", "No files", 422)
    document_id, display_name, meta = store_document(
        g.db, current_app.config["DATA_ROOT"], files, state="inbox", on_insert=_enqueue)
    return ok({"document_id": document_id, "pages": meta["pages"],
               "display_name": display_name, "qr": None,
               "processing": document_jobs.metadata(g.db, document_id)})


@bp.get("/api/documents/inbox")
@admin_required
def inbox():
    rows = g.db.execute(
        "SELECT document_id, sha256, size_bytes, page_count, state,"
        " display_name, created_at FROM documents WHERE state='inbox'"
        " ORDER BY created_at").fetchall()
    return ok({"documents": [dict(r) | {"qr": qr_capture.metadata(g.db, r["document_id"]),
                                       "processing": document_jobs.metadata(g.db, r["document_id"])}
                             for r in rows]})


@bp.get("/api/documents/<document_id>/stream")
@admin_required
def stream(document_id):
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


@bp.get("/documents/<document_id>/preview")
@admin_required
def preview(document_id):
    row = g.db.execute(
        "SELECT display_name FROM documents WHERE document_id = ?",
        (document_id,)).fetchone()
    if row is None:
        abort(404)
    return render_template("document_preview.html", name=row["display_name"],
                           stream_url=url_for("capture.stream", document_id=document_id))


@bp.post("/api/documents/<document_id>/discard")
@admin_required
def discard(document_id):
    row = g.db.execute(
        "SELECT state, rel_path FROM documents WHERE document_id = ?",
        (document_id,)).fetchone()
    if not row or row["state"] != "inbox":
        return err("not_in_inbox", "Document not in inbox", 409)
    g.db.execute("UPDATE documents SET state='discarded' WHERE"
                 " document_id = ?", (document_id,))
    g.db.commit()
    return ok({"document_id": document_id})


@bp.get("/api/documents/<document_id>/thumbnail")
def thumbnail(document_id):
    # The stream route checks admin access, existence, and content integrity.
    response = stream(document_id)
    if not hasattr(response, "status_code") or response.status_code != 200:
        return response
    response.close()
    row = g.db.execute("SELECT rel_path, sha256 FROM documents WHERE document_id=?",
                       (document_id,)).fetchone()
    try:
        out = document_thumbnail(current_app.config["DATA_ROOT"] / row["rel_path"])
    except Exception:
        return err("thumbnail_unavailable", "Open the PDF to view this document", 404)
    response = send_file(out, mimetype="image/jpeg", etag=row["sha256"])
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response
