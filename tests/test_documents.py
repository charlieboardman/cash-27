"""Document lifecycle: capture limits, per-format normalization,
type detection by content, multi-document independence, state
transitions, hash verification, recovery reconciliation."""

import io
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from cash.db import apply_migrations, connect
from cash.web.app import create_app
from cash.web.documents import document_rel_path, recover_documents


def jpeg_bytes():
    b = io.BytesIO()
    Image.new("RGB", (64, 32), "blue").save(b, "JPEG")
    b.seek(0)
    return b


def pdf_bytes(pages=1):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(200, 200)
    b = io.BytesIO()
    w.write(b)
    b.seek(0)
    return b


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, admin_password="x", testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf(client):
    return client.post("/api/login", json={"password": "x"}
                       ).get_json()["data"]["csrf"]


class TestCapture:
    def test_storage_name_converts_to_utc_and_sanitizes_its_hint(self):
        pacific = timezone(timedelta(hours=-7))
        assert document_rel_path(
            "D000042", "Résumé Q3.PDF",
            datetime(2026, 9, 4, 10, 23, tzinfo=pacific), 2,
        ) == "documents/20260904T172300Z--D000042--02--resume-q3.pdf"

    def test_relative_data_root_can_stream_documents(
            self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        app = create_app("relative-data", admin_password="x", testing=True)
        assert app.config["DATA_ROOT"] == tmp_path / "relative-data"
        relative_client = app.test_client()
        token = relative_client.post(
            "/api/login", json={"password": "x"}).get_json()["data"]["csrf"]
        uploaded = relative_client.post(
            "/api/documents/capture",
            data={"files": [(jpeg_bytes(), "receipt.jpg")]},
            content_type="multipart/form-data", headers={"X-CSRF": token})
        document_id = uploaded.get_json()["data"]["documents"][0]["document_id"]
        streamed = relative_client.get(
            f"/api/documents/{document_id}/stream")
        assert streamed.status_code == 200
        assert streamed.mimetype == "application/pdf"

    def test_capture_card_merges_selected_files_in_order(self, client, csrf):
        r = client.post("/api/documents/capture-merge",
                        data={"files": [(pdf_bytes(1), "first.pdf"),
                                        (jpeg_bytes(), "second.jpg")]},
                        content_type="multipart/form-data",
                        headers={"X-CSRF": csrf})
        assert r.status_code == 200
        data = r.get_json()["data"]
        assert data["pages"] == 2
        assert data["display_name"] == "first.pdf + 1 more"
        with connect(client.application.config["DATA_ROOT"] / "cash.db") as db:
            rel_path = db.execute(
                "SELECT rel_path FROM documents WHERE document_id=?",
                (data["document_id"],)).fetchone()[0]
        assert re.fullmatch(
            r"documents/\d{8}T\d{6}Z--D000001--first-plus-1-more\.pdf",
            rel_path)
        streamed = client.get(
            f"/api/documents/{data['document_id']}/stream")
        from pypdf import PdfReader
        pages = PdfReader(io.BytesIO(streamed.data)).pages
        assert float(pages[0].mediabox.width) == 200
        assert float(pages[0].mediabox.height) == 200
        assert float(pages[1].mediabox.width) > float(pages[1].mediabox.height)

    def test_multi_format_one_request_independent(self, client, csrf):
        r = client.post("/api/documents/capture",
                        data={"files": [(pdf_bytes(2), "a.pdf"),
                                        (jpeg_bytes(), "b.jpg"),
                                        (jpeg_bytes(), "c.jpg")]},
                        content_type="multipart/form-data",
                        headers={"X-CSRF": csrf})
        docs = r.get_json()["data"]["documents"]
        assert len(docs) == 3
        assert docs[0]["pages"] == 2
        assert [doc["display_name"] for doc in docs] == [
            "a.pdf", "b.jpg", "c.jpg"]
        assert [doc["document_id"] for doc in docs] == [
            "D000001", "D000002", "D000003"]
        with connect(client.application.config["DATA_ROOT"] / "cash.db") as db:
            paths = [row[0] for row in db.execute(
                "SELECT rel_path FROM documents ORDER BY document_id")]
        assert re.fullmatch(
            r"documents/(\d{8}T\d{6}Z)--D000001--01--a\.pdf", paths[0])
        stamp = paths[0].split("--", 1)[0]
        assert paths[1] == f"{stamp}--D000002--02--b.pdf"
        assert paths[2] == f"{stamp}--D000003--03--c.pdf"

    def test_content_sniffed_not_extension(self, client, csrf):
        r = client.post("/api/documents/capture",
                        data={"files": [(jpeg_bytes(), "evil.pdf")]},
                        content_type="multipart/form-data",
                        headers={"X-CSRF": csrf})
        assert r.status_code == 200  # treated as image, valid

    def test_garbage_rejected(self, client, csrf):
        r = client.post("/api/documents/capture",
                        data={"files": [(io.BytesIO(b"MZ\x90\x00"), "x.exe")]},
                        content_type="multipart/form-data",
                        headers={"X-CSRF": csrf})
        assert r.get_json()["ok"] is False

    def test_failed_capture_does_not_reuse_reserved_id(self, client, csrf):
        rejected = client.post(
            "/api/documents/capture",
            data={"files": [(io.BytesIO(b"bad"), "bad.exe")]},
            content_type="multipart/form-data", headers={"X-CSRF": csrf})
        assert rejected.status_code == 422
        accepted = client.post(
            "/api/documents/capture",
            data={"files": [(pdf_bytes(), "receipt.pdf")]},
            content_type="multipart/form-data", headers={"X-CSRF": csrf})
        assert accepted.get_json()["data"]["documents"][0]["document_id"] == \
            "D000002"

    def test_failed_multi_capture_removes_all_partial_objects(
            self, client, csrf):
        rejected = client.post(
            "/api/documents/capture",
            data={"files": [(pdf_bytes(), "valid.pdf"),
                            (io.BytesIO(b"bad"), "bad.exe")]},
            content_type="multipart/form-data", headers={"X-CSRF": csrf})
        assert rejected.status_code == 422
        root = Path(client.application.config["DATA_ROOT"])
        with connect(root / "cash.db") as db:
            assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
            assert db.execute(
                "SELECT last_value FROM document_id_sequence").fetchone()[0] == 2
        assert not list((root / "documents").glob("*.pdf"))

    def test_stream_verify_hash(self, client, csrf, tmp_path):
        r = client.post("/api/documents/capture",
                        data={"files": [(pdf_bytes(1), "a.pdf")]},
                        content_type="multipart/form-data",
                        headers={"X-CSRF": csrf})
        doc = r.get_json()["data"]["documents"][0]
        r2 = client.get(f"/api/documents/{doc['document_id']}/stream")
        assert r2.status_code == 200
        # Corrupt the immutable object; its internal path is not exposed by
        # the upload API.
        with connect(tmp_path / "cash.db") as db:
            rel_path = db.execute(
                "SELECT rel_path FROM documents WHERE document_id=?",
                (doc["document_id"],)).fetchone()[0]
        f = tmp_path / rel_path
        f.write_bytes(f.read_bytes() + b"corrupt")
        r3 = client.get(f"/api/documents/{doc['document_id']}/stream")
        assert r3.status_code == 410

    def test_discard_lifecycle(self, client, csrf):
        r = client.post("/api/documents/capture",
                        data={"files": [(jpeg_bytes(), "a.jpg")]},
                        content_type="multipart/form-data",
                        headers={"X-CSRF": csrf})
        doc = r.get_json()["data"]["documents"][0]["document_id"]
        with connect(client.application.config["DATA_ROOT"] / "cash.db") as db:
            rel_path = db.execute(
                "SELECT rel_path FROM documents WHERE document_id=?",
                (doc,)).fetchone()[0]
        stored = Path(client.application.config["DATA_ROOT"]) / rel_path
        assert stored.is_file()
        assert client.post(f"/api/documents/{doc}/discard",
                           headers={"X-CSRF": csrf}).status_code == 200
        # Discard is logical metadata; immutable bytes are retained.
        assert stored.is_file()
        # again rejected
        assert client.post(f"/api/documents/{doc}/discard",
                           headers={"X-CSRF": csrf}).status_code == 409


class TestRecovery:
    def test_schema_allows_only_canonical_paths_and_logical_states(
            self, tmp_path):
        create_app(tmp_path, admin_password="x", testing=True)
        connection = connect(tmp_path / "cash.db")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO documents (document_id, rel_path, sha256,"
                " size_bytes, state) VALUES"
                " ('d1', 'inbox/d1.pdf', 'hash', 1, 'inbox')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO documents (document_id, rel_path, sha256,"
                " size_bytes, state) VALUES"
                " ('d3', 'documents/nested/d3.pdf', 'hash', 1, 'inbox')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO documents (document_id, rel_path, sha256,"
                " size_bytes, state) VALUES"
                " ('d2', 'documents/d2.pdf', 'hash', 1, 'staged')")
        connection.close()

    def test_missing_filed_document(self, tmp_path):
        app = create_app(tmp_path, admin_password="x", testing=True)
        conn = connect(tmp_path / "cash.db")
        conn.execute(
            "INSERT INTO documents (document_id, rel_path, sha256,"
            " size_bytes, state) VALUES ('d1', 'documents/d1.pdf',"
            " 'deadbeef', 1, 'filed')")
        conn.commit()
        findings = recover_documents(tmp_path, conn)
        assert any("document file missing" in f for f in findings)
        state = conn.execute(
            "SELECT state FROM documents WHERE document_id='d1'"
        ).fetchone()[0]
        assert state == "missing"

    def test_orphan_detected(self, tmp_path):
        app = create_app(tmp_path, admin_password="x", testing=True)
        conn = connect(tmp_path / "cash.db")
        (tmp_path / "documents" / "orphan.pdf").write_bytes(b"x")
        findings = recover_documents(tmp_path, conn)
        assert any("orphan file" in f for f in findings)

    def test_capture_uses_one_readable_immutable_object_directory(
            self, client, csrf, tmp_path):
        response = client.post(
            "/api/documents/capture",
            data={"files": [(pdf_bytes(), "Human readable name.pdf")]},
            content_type="multipart/form-data",
            headers={"X-CSRF": csrf})
        document = response.get_json()["data"]["documents"][0]
        document_id = document["document_id"]
        connection = connect(tmp_path / "cash.db")
        row = connection.execute(
            "SELECT rel_path, display_name, state FROM documents"
            " WHERE document_id=?", (document_id,)).fetchone()
        connection.close()
        assert document_id == "D000001"
        assert row["display_name"] == "Human readable name.pdf"
        assert row["state"] == "inbox"
        assert re.fullmatch(
            r"documents/\d{8}T\d{6}Z--D000001--human-readable-name\.pdf",
            row["rel_path"])
        assert (tmp_path / row["rel_path"]).is_file()
        assert not (tmp_path / "inbox").exists()
