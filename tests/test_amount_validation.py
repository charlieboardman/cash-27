"""Malformed amounts are validation errors, never server errors."""

import uuid

import pytest

from cash.web.app import create_app


@pytest.fixture
def client(tmp_path):
    client = create_app(tmp_path, admin_password="pw", testing=True).test_client()
    csrf = client.post("/api/login", json={"password": "pw"}).get_json()["data"]["csrf"]
    client.environ_base["HTTP_X_CSRF"] = csrf
    assert client.post("/api/entries/opening", json={
        "submission_id": str(uuid.uuid4()), "date": "2026-09-01",
        "usd": "100", "mxn": "0"}).status_code == 200
    return client


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "abc"])
def test_non_numeric_amounts_are_rejected(client, amount):
    entry = client.post("/api/entries/donation", json={
        "submission_id": str(uuid.uuid4()), "date": "2026-09-02", "usd": amount})
    assert entry.status_code == 400
    for field in ("usd", "assigned_rate"):
        edit = client.patch("/api/ledger/1", json={
            "submission_id": str(uuid.uuid4()), "expected_version": 1,
            field: amount, "reason": "Typo"})
        assert edit.status_code == 422, (field, edit.get_json())
