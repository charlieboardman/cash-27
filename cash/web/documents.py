"""Immutable document-object capture and integrity checks (spec 9, 15).

Every normalized PDF is published once under a UTC-stamped, readable object
name. Inbox, filing, type, and business relationships are SQLite metadata;
changing them never renames or rewrites the PDF.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
import threading
from contextlib import closing
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from ..commands import ValidationFailure

LIMITS = {"file_bytes": 20 * 1024 * 1024, "request_bytes": 64 * 1024 * 1024,
          "documents_per_request": 20, "pages_per_document": 40}
DOCUMENT_ID_RE = re.compile(r"^D[0-9]{6,}$")
OBJECT_NAME_RE = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z--D[0-9]{6,}(?:--[0-9]{2})?"
    r"--[a-z0-9]+(?:-[a-z0-9]+)*\.pdf$")


# PDFium is not thread-safe, including when rendering unrelated documents.
_thumbnail_lock = threading.Lock()


def document_thumbnail(path: Path) -> io.BytesIO:
    """Render a small first-page preview under the shared PDFium lock."""
    import pypdfium2 as pdfium

    with _thumbnail_lock, closing(pdfium.PdfDocument(str(path))) as pdf:
        with closing(pdf[0]) as page:
            with closing(page.render(scale=min(1, 180 / max(page.get_size())))) as bitmap:
                out = io.BytesIO()
                bitmap.to_pil().convert("RGB").save(out, format="JPEG", quality=75)
    out.seek(0)
    return out


def reserve_document_ids(conn, count: int) -> list[str]:
    """Durably reserve monotonic IDs. Gaps after failed uploads are valid."""
    if count < 1:
        raise ValueError("Document ID reservation must be positive")
    if conn.in_transaction:
        raise RuntimeError("Document IDs must be reserved outside a command")
    conn.execute("BEGIN IMMEDIATE")
    try:
        first = conn.execute(
            "SELECT last_value + 1 FROM document_id_sequence"
            " WHERE singleton_id=1").fetchone()[0]
        conn.execute(
            "UPDATE document_id_sequence SET last_value=last_value+?"
            " WHERE singleton_id=1", (count,))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return [f"D{number:06d}" for number in range(first, first + count)]


def _storage_slug(display_name: str) -> str:
    """Create a short portable hint; it is never treated as authority."""
    readable = display_name.replace(" + ", " plus ")
    readable = re.sub(r"\.(?:pdf|jpe?g|png)(?=$|\s)", "", readable,
                      flags=re.IGNORECASE)
    ascii_name = unicodedata.normalize("NFKD", readable).encode(
        "ascii", "ignore").decode("ascii").lower()
    slug = "-".join(re.findall(r"[a-z0-9]+", ascii_name))[:72].strip("-")
    return slug or "document"


def document_rel_path(document_id: str, display_name: str,
                      captured_at: datetime,
                      batch_position: int | None = None) -> str:
    """Build an immutable UTC-stamped object path for a reserved ID."""
    if not DOCUMENT_ID_RE.fullmatch(document_id):
        raise ValidationFailure("invalid_document_id", "Document ID is invalid")
    if captured_at.tzinfo is None:
        raise ValueError("Document capture time must include a timezone")
    if batch_position is not None and not 1 <= batch_position <= 99:
        raise ValueError("Document batch position must be between 1 and 99")
    stamp = captured_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    position = f"--{batch_position:02d}" if batch_position is not None else ""
    name = f"{stamp}--{document_id}{position}--{_storage_slug(display_name)}.pdf"
    if not OBJECT_NAME_RE.fullmatch(name):
        raise ValidationFailure("invalid_document_name",
                                "Document storage name is invalid")
    return f"documents/{name}"


def _publish_pdf(contents: bytes, out_dir: Path, rel_path: str) -> Path:
    """Durably publish immutable bytes without overwriting another object.

    The temporary file and final object are in one directory. A hard link
    makes the final name visible atomically and refuses to overwrite an object.
    """
    relative = Path(rel_path)
    if relative.parent != Path("documents") or \
            not OBJECT_NAME_RE.fullmatch(relative.name):
        raise ValidationFailure("invalid_document_name",
                                "Document storage name is invalid")
    out_dir.mkdir(parents=True, exist_ok=True)
    object_name = relative.name
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


def normalize_document_parts(fileobjs, out_dir: Path,
                             rel_path: str) -> dict:
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

    path = _publish_pdf(merged, out_dir, rel_path)
    return {
        "path": str(path), "sha256": hashlib.sha256(merged).hexdigest(),
        "size": len(merged), "pages": pages,
    }


def store_document(conn, data_root: Path, files, *, state: str,
                   voucher_id: str | None = None, on_insert=None):
    """Publish one PDF built from the uploaded files, in order, and record it.
    on_insert(document_id, rel_path, meta, captured_at) runs in the same
    transaction. If the record cannot be written, the PDF is removed again.
    Returns (document_id, display_name, meta)."""
    captured_at = datetime.now(timezone.utc)
    display_name = upload_display_name(files[0].filename, len(files))
    document_id = reserve_document_ids(conn, 1)[0]
    rel_path = document_rel_path(document_id, display_name, captured_at)
    meta = normalize_document_parts([f.stream for f in files],
                                    data_root / "documents", rel_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO documents (document_id, voucher_id, rel_path, sha256,"
            " size_bytes, page_count, display_name, state) VALUES (?,?,?,?,?,?,?,?)",
            (document_id, voucher_id, rel_path, meta["sha256"], meta["size"],
             meta["pages"], display_name, state))
        if on_insert:
            on_insert(document_id, rel_path, meta, captured_at)
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        (data_root / rel_path).unlink(missing_ok=True)
        raise
    return document_id, display_name, meta


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
        from ..document_jobs import object_refs
        known = {r['rel_path'] for r in object_refs(conn)}
        for f in docs_dir.glob("*.pdf"):
            rel = f.relative_to(data_root)
            if str(rel) not in known:
                findings.append(f"orphan file {rel}")
    return findings
