"""Document lifecycle: capture limits, per-format normalization,
type detection by content, multi-document independence, state
transitions, hash verification, recovery reconciliation."""

import io
import sqlite3
import tempfile
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from cash.db import apply_migrations, connect
from cash.web.app import create_app
from cash.web.documents import recover_documents


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
        assert len({d["document_id"] for d in docs}) == 3

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

    def test_stream_verify_hash(self, client, csrf, tmp_path):
        r = client.post("/api/documents/capture",
                        data={"files": [(pdf_bytes(1), "a.pdf")]},
                        content_type="multipart/form-data",
                        headers={"X-CSRF": csrf})
        doc = r.get_json()["data"]["documents"][0]
        r2 = client.get(f"/api/documents/{doc['document_id']}/stream")
        assert r2.status_code == 200
        # corrupt the file
        p = tmp_path / doc["path"].split("/")[-1] if False else None
        rel = Path(doc["path"]).name
        f = tmp_path / "inbox" / rel
        f.write_bytes(f.read_bytes() + b"corrupt")
        r3 = client.get(f"/api/documents/{doc['document_id']}/stream")
        assert r3.status_code == 410

    def test_discard_lifecycle(self, client, csrf):
        r = client.post("/api/documents/capture",
                        data={"files": [(jpeg_bytes(), "a.jpg")]},
                        content_type="multipart/form-data",
                        headers={"X-CSRF": csrf})
        doc = r.get_json()["data"]["documents"][0]["document_id"]
        assert client.post(f"/api/documents/{doc}/discard",
                           headers={"X-CSRF": csrf}).status_code == 200
        # again rejected
        assert client.post(f"/api/documents/{doc}/discard",
                           headers={"X-CSRF": csrf}).status_code == 409


class TestRecovery:
    def test_missing_filed_document(self, tmp_path):
        app = create_app(tmp_path, admin_password="x", testing=True)
        conn = connect(tmp_path / "cash.db")
        conn.execute(
            "INSERT INTO documents (document_id, rel_path, sha256,"
            " size_bytes, state) VALUES ('d1', 'documents/FY26/x.pdf',"
            " 'deadbeef', 1, 'filed')")
        conn.commit()
        findings = recover_documents(tmp_path, conn)
        assert any("filed file missing" in f for f in findings)
        state = conn.execute(
            "SELECT state FROM documents WHERE document_id='d1'"
        ).fetchone()[0]
        assert state == "missing"

    def test_orphan_detected(self, tmp_path):
        app = create_app(tmp_path, admin_password="x", testing=True)
        conn = connect(tmp_path / "cash.db")
        (tmp_path / "documents" / "FY26").mkdir(parents=True,
                                                exist_ok=True)
        (tmp_path / "documents" / "FY26" / "orphan.pdf").write_bytes(b"x")
        findings = recover_documents(tmp_path, conn)
        assert any("orphan file" in f for f in findings)
