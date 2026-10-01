"""Fixtures shared across test modules."""

import threading
import uuid

import pytest
from werkzeug.serving import make_server

from cash.web.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, admin_password="x", testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf(client):
    return client.post("/api/login", json={"password": "x"}).get_json()["data"]["csrf"]


@pytest.fixture
def serve():
    """Serve apps on random loopback ports: serve(app) returns the origin."""
    running = []

    def start(app):
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        running.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}"
    yield start
    for server, thread in running:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    """Chromium through Playwright; tests that need it skip without it."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def web(browser, serve, tmp_path):
    """A logged-in administrator page on a fresh ledger with starting cash and
    one class and account code. Yields (page, post, tmp_path); fails on any
    uncaught page error."""
    origin = serve(create_app(tmp_path / "data", admin_password="test", testing=True))
    context = browser.new_context(base_url=origin, viewport={"width": 1440, "height": 1000})
    csrf = context.request.post("/api/login", data={"password": "test"}).json()["data"]["csrf"]
    context.set_extra_http_headers({"X-CSRF": csrf})
    # Deleting a row or removing a receipt waits for Undo; keep the wait short.
    context.add_init_script("window.cashUndoMs = 1500")

    def post(path, **data):
        response = context.request.post(path, data={"submission_id": str(uuid.uuid4()), **data})
        assert response.ok, response.text()
        return response.json()["data"]

    post("/api/entries/opening", date="2026-09-01", usd="2000", mxn="15000",
         assigned_rate_milli=15000)
    post("/api/settings/classes", class_code="7.5", name="Infant Orphanage")
    post("/api/settings/expense-accounts", account_id="1000", name="Petty Cash Fund")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        yield page, post, tmp_path
        assert not errors
    finally:
        context.close()
