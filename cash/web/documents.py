"""Immutable document-object capture and integrity checks (spec 9, 15).

Every normalized PDF is published once at ``documents/<document_id>.pdf``.
Inbox, filing, type, and business relationships are SQLite metadata; changing
them never renames or rewrites the PDF.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import uuid
from pathlib import Path

from ..commands import ValidationFailure

LIMITS = {"file_bytes": 20 * 1024 * 1024, "request_bytes": 64 * 1024 * 1024,
          "documents_per_request": 20, "pages_per_document": 40}


def document_rel_path(document_id: str) -> str:
    """Return the opaque canonical object key for a document."""
    try:
        normalized = str(uuid.UUID(document_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationFailure(
            "invalid_document_id", "Document ID is invalid") from exc
    return f"documents/{normalized}.pdf"


def _publish_pdf(contents: bytes, out_dir: Path, document_id: str) -> Path:
    """Durably publish immutable bytes without overwriting another object.

    The temporary file and final object are in one directory.  A hard link
    makes the final name visible atomically and fails if that UUID somehow
    already exists.  The database row is inserted only after this succeeds.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    object_name = Path(document_rel_path(document_id)).name
    path = out_dir / object_name
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{object_name}.", suffix=".tmp", dir=out_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValidationFailure(
                "duplicate_document", "Document already exists") from exc
        directory_fd = os.open(out_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def upload_display_name(filename: str | None, file_count: int = 1) -> str:
    """Return a safe, recognizable label without using it as a path."""
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = " ".join(name.replace("\x00", "").split()).strip()[:180]
    if not name:
        name = "Captured document"
    if file_count > 1:
        name = f"{name} + {file_count - 1} more"
    return name

def merge_to_pdf(items: list[tuple[bytes, str]]) -> bytes:
    """Merge several files into one real multi-page PDF in order
    (spec 9): PDFs contribute their pages; images become one page
    each. Never byte-concatenation."""
    import io
    from pypdf import PdfReader, PdfWriter
    from PIL import Image
    writer = PdfWriter()
    for data, mime in items:
        if mime == "application/pdf":
            reader = PdfReader(io.BytesIO(data))
            for page in reader.pages:
                writer.add_page(page)
        else:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            one = io.BytesIO()
            img.save(one, "PDF", resolution=150.0)
            one.seek(0)
            for page in PdfReader(one).pages:
                writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.read()


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

    if mime == "application/pdf":
        reader = PdfReader(io.BytesIO(bytes(data)))
        writer = PdfWriter()
        if len(reader.pages) > LIMITS["pages_per_document"]:
            raise ValidationFailure("too_many_pages", "Page limit")
        for page in reader.pages:
            writer.add_page(page)
        normalized = io.BytesIO()
        writer.write(normalized)
        contents = normalized.getvalue()
        pages = len(reader.pages)
    else:
        # image -> one single-page PDF via Pillow's encoder
        from PIL import Image
        img = Image.open(io.BytesIO(bytes(data)))
        if img.format not in ("JPEG", "PNG"):
            raise ValidationFailure("unsupported_type", "JPEG or PNG image")
        img = img.convert("RGB")
        normalized = io.BytesIO()
        img.save(normalized, "PDF", resolution=150.0)
        contents = normalized.getvalue()
        pages = 1
    path = _publish_pdf(contents, out_dir, document_id)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": str(path), "sha256": digest,
            "size": path.stat().st_size, "pages": pages}


def normalize_document_parts(fileobjs, out_dir: Path,
                             document_id: str) -> dict:
    """Build one ordered PDF from several multipart uploads.

    Images contribute one page and PDFs contribute all of their pages. The
    browser sends ordinary multipart bytes; validation and PDF construction
    remain server-side.
    """
    from pypdf import PdfReader

    fileobjs = list(fileobjs)
    if not fileobjs:
        raise ValidationFailure("no_files", "Add at least one photo or file")
    if len(fileobjs) > LIMITS["documents_per_request"]:
        raise ValidationFailure(
            "too_many_files", "Too many photos or files in one receipt")

    items = []
    request_total = 0
    for fileobj in fileobjs:
        data = bytearray()
        file_total = 0
        while True:
            chunk = fileobj.read(1 << 16)
            if not chunk:
                break
            file_total += len(chunk)
            request_total += len(chunk)
            if file_total > LIMITS["file_bytes"]:
                raise ValidationFailure(
                    "file_too_large", "One photo or file is too large")
            if request_total > LIMITS["request_bytes"]:
                raise ValidationFailure(
                    "request_too_large", "The combined receipt is too large")
            data.extend(chunk)
        mime = sniff_type(bytes(data[:8]))
        if mime is None:
            raise ValidationFailure(
                "unsupported_type", "Use PDF, JPEG, or PNG files")
        items.append((bytes(data), mime))

    try:
        merged = merge_to_pdf(items)
        pages = len(PdfReader(io.BytesIO(merged)).pages)
    except Exception as exc:
        raise ValidationFailure(
            "invalid_document", "A photo or PDF could not be read") from exc
    if not pages:
        raise ValidationFailure("empty_document", "The receipt has no pages")
    if pages > LIMITS["pages_per_document"]:
        raise ValidationFailure(
            "too_many_pages", "The receipt has too many pages")

    path = _publish_pdf(merged, out_dir, document_id)
    return {
        "path": str(path), "sha256": hashlib.sha256(merged).hexdigest(),
        "size": len(merged), "pages": pages,
    }


def recover_documents(data_root: Path, conn) -> list[str]:
    """Report missing, changed, and unreferenced immutable objects.

    Returns a list of human-readable findings."""
    findings = []
    docs_dir = data_root / "documents"
    for row in conn.execute(
            "SELECT document_id, rel_path, state, sha256 FROM documents"):
        path = data_root / row["rel_path"]
        if row["state"] != "missing" and not path.exists():
            findings.append(f"{row['document_id']}: document file missing")
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
        for f in docs_dir.glob("*.pdf"):
            rel = f.relative_to(data_root)
            if str(rel) not in known:
                findings.append(f"orphan file {rel}")
    return findings
