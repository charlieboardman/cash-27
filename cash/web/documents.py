"""Document capture and filing (spec 9, 14).

Inbox: one row per uploaded document, state transitions
inbox -> staged -> filed / discarded / missing. Filing is recoverable:
the PDF moves to its final path inside the same SQLite transaction that
flips state; startup recovery reconciles staged rows, orphan files, and
hash mismatches.
"""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from pathlib import Path

from ..commands import ValidationFailure

LIMITS = {"file_bytes": 20 * 1024 * 1024, "request_bytes": 64 * 1024 * 1024,
          "documents_per_request": 20, "pages_per_document": 40}

CONTENT_TYPES = {
    b"%PDF": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}


def sniff_type(header: bytes) -> str | None:
    for magic, mime in CONTENT_TYPES.items():
        if header.startswith(magic):
            return mime
    return None


def normalize_document(fileobj, out_dir: Path, document_id: str) -> dict:
    """Read one upload, build/validate a real multi-page PDF,
    return {path, sha256, size, pages}. Raises ValidationFailure."""
    from pypdf import PdfReader, PdfWriter

    data = bytearray()
    total = 0
    while True:
        chunk = fileobj.read(1 << 16)
        if not chunk:
            break
        total += len(chunk)
        if total > LIMITS["file_bytes"]:
            raise ValidationFailure("file_too_large", "File limit")
        data.extend(chunk)
    mime = sniff_type(bytes(data[:8]))
    if mime is None:
        raise ValidationFailure("unsupported_type",
                                "PDF, JPEG, or PNG only")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{document_id}.pdf"
    if path.exists():
        raise ValidationFailure("duplicate_document",
                                "Document exists")

    if mime == "application/pdf":
        reader = PdfReader(io.BytesIO(bytes(data)))
        writer = PdfWriter()
        if len(reader.pages) > LIMITS["pages_per_document"]:
            raise ValidationFailure("too_many_pages", "Page limit")
        for page in reader.pages:
            writer.add_page(page)
        with open(path, "wb") as f:
            writer.write(f)
        pages = len(reader.pages)
    else:
        # image -> one single-page PDF via Pillow's encoder
        from PIL import Image
        img = Image.open(io.BytesIO(bytes(data)))
        if img.format not in ("JPEG", "PNG"):
            raise ValidationFailure("unsupported_type", "JPEG or PNG image")
        img = img.convert("RGB")
        img.save(path, "PDF", resolution=150.0)
        pages = 1
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "sha256": digest,
            "size": path.stat().st_size, "pages": pages}


def file_document(conn, document_id: str, dest_rel: str):
    """Move an inbox document to its final path atomically with its
    state change. Caller holds BEGIN IMMEDIATE."""
    d = conn.execute(
        "SELECT rel_path, state FROM documents WHERE document_id = ?",
        (document_id,)).fetchone()
    if not d or d["state"] != "inbox":
        raise ValidationFailure("not_in_inbox", "Document not in inbox")
    conn.execute(
        "UPDATE documents SET state='staged', rel_path=? WHERE "
        "document_id=?", (dest_rel, document_id))
    return d["rel_path"]


def recover_documents(data_root: Path, conn) -> list[str]:
    """Startup: reconcile staged rows, orphan files, hash mismatches.

    Returns a list of human-readable findings."""
    findings = []
    inbox_dir = data_root / "inbox"
    docs_dir = data_root / "documents"
    for row in conn.execute(
            "SELECT document_id, rel_path, state, sha256 FROM documents"):
        path = data_root / row["rel_path"]
        if row["state"] == "staged":
            if path.exists():
                findings.append(
                    f"{row['document_id']}: staged file present, leaving "
                    f"staged (recover on next command)")
            else:
                findings.append(
                    f"{row['document_id']}: staged file missing -> missing")
                conn.execute(
                    "UPDATE documents SET state='missing' WHERE "
                    "document_id=?", (row["document_id"],))
        elif row["state"] == "filed" and not path.exists():
            findings.append(f"{row['document_id']}: filed file missing")
            conn.execute("UPDATE documents SET state='missing' WHERE "
                         "document_id=?", (row["document_id"],))
        elif path.exists():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != row["sha256"]:
                findings.append(
                    f"{row['document_id']}: hash mismatch")
    if docs_dir.exists():
        known = {r[0] for r in conn.execute(
            "SELECT rel_path FROM documents")}
        for f in docs_dir.rglob("*.pdf"):
            rel = f.relative_to(data_root)
            if str(rel).startswith(("inbox", "staging")):
                continue
            if str(rel) not in known:
                findings.append(f"orphan file {rel}")
    return findings
