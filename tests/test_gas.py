"""The Drive backup script (gas/Code.gs) against a live Cash 27 server, with
Google's services replaced by tests/gas_harness.js."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import uuid

import pytest
from werkzeug.serving import make_server

from cash import sync, voucher_defaults
from cash.db import connect
from cash.web.app import create_app

HARNESS = Path(__file__).with_name("gas_harness.js")
pytestmark = pytest.mark.skipif(not shutil.which("node") or not shutil.which("curl"),
                                reason="needs node and curl")


class Desk:
    """A Cash 27 server with a little ledger, and the script's saved state."""

    def __init__(self, root: Path, state: Path):
        self.root, self.state = root, state
        self.app = create_app(root, admin_password="x", testing=True)
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.token = sync.create_token(root)
        state.write_text(json.dumps({"props": {
            "CASH_URL": f"http://127.0.0.1:{self.server.server_port}/",
            "CASH_TOKEN": self.token}}))
        self.conn = connect(root / "cash.db")

    def txn(self, **extra):
        values = dict(movement_id=str(uuid.uuid4()), submission_id=str(uuid.uuid4()),
                      date="2026-09-02", txn_type="expense", from_account="SAFE",
                      to_account="EXP_SUPPLIES", usd_cents=0, mxn_centavos=0)
        values.update(extra)
        return self.conn.execute(
            f"INSERT INTO transactions ({','.join(values)}) VALUES ({','.join('?' * len(values))})",
            list(values.values())).lastrowid

    def document(self, label: str, content: bytes) -> str:
        did = str(uuid.uuid4())
        rel_path = f"documents/20260902T000000Z--{label}.pdf"
        (self.root / rel_path).write_bytes(content)
        self.conn.execute(
            "INSERT INTO documents (document_id, rel_path, sha256, size_bytes, state)"
            " VALUES (?,?,?,?,'filed')",
            (did, rel_path, hashlib.sha256(content).hexdigest(), len(content)))
        return did

    def run(self, *functions: str) -> str | None:
        out = subprocess.run(["node", str(HARNESS), str(self.state), *functions],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)["error"]

    @property
    def saved(self) -> dict:
        return json.loads(self.state.read_text())

    def edit(self, change):
        state = self.saved
        change(state)
        self.state.write_text(json.dumps(state))

    def close(self):
        self.conn.close()
        self.server.shutdown()
        self.thread.join(timeout=5)


@pytest.fixture
def desk(tmp_path):
    desk = Desk(tmp_path / "data", tmp_path / "gas-state.json")
    desk.txn(date="2026-08-01", txn_type="opening", from_account="OPENING_EQUITY",
             to_account="SAFE", usd_cents=100000, mxn_centavos=155000,
             assigned_rate_milli=15500)
    receipt = desk.document("D000001--receipt", b"%PDF receipt")
    tid = desk.txn(usd_cents=1250, vendor="=Vendor", description="Paint",
                   class_code=None)
    desk.conn.execute("INSERT INTO transaction_documents (txn_id, document_id, role)"
                      " VALUES (?, ?, 'support')", (tid, receipt))
    desk.document("D000002--inbox", b"%PDF waiting in the inbox")
    voucher_defaults.save_default(desk.root, "Ana")
    yield desk
    desk.close()


def children(state, parent, name=None):
    return [item for item in state["items"].values()
            if item["parent"] == parent and not item["trashed"]
            and (name is None or item["name"] == name)]


def folder(state, *names):
    parent = "root"
    for name in names:
        [found] = [item for item in children(state, parent, name) if item["folder"]]
        parent = found["id"]
    return parent


def files(state, *names):
    return {item["name"]: item for item in children(state, folder(state, *names))
            if not item["folder"]}


def rows(state, spreadsheet_id, tab):
    [sheet] = [s for s in state["spreadsheets"][spreadsheet_id]["sheets"] if s["name"] == tab]
    return sheet, [[cell["value"] for cell in row] for row in sheet["rows"]]


def fiscal_label(desk):
    local = datetime.now(desk.app.config["TIMEZONE"]).date().isoformat()
    from cash.prep import fiscal_year_for
    return sync.fiscal_label(fiscal_year_for(local))


def test_setup_then_a_full_nightly_copy(desk):
    assert desk.run("setup") is None
    state = desk.saved
    assert state["triggers"] == [{"handler": "nightlySync", "everyDays": 1, "atHour": 2}]
    assert set(files(state, "Cash 27")) == {"Sync log"}
    # Setting up again changes nothing.
    assert desk.run("setup") is None
    assert len(desk.saved["triggers"]) == 1
    assert len(children(desk.saved, "root", "Cash 27")) == 1

    assert desk.run("nightlySync") is None
    state = desk.saved
    documents = files(state, "Cash 27", "Documents")
    assert set(documents) == {"20260902T000000Z--D000001--receipt.pdf",
                              "20260902T000000Z--D000002--inbox.pdf"}
    receipt = documents["20260902T000000Z--D000001--receipt.pdf"]
    assert base64.b64decode(receipt["content"]) == b"%PDF receipt"
    assert receipt["description"] == "sha256 " + hashlib.sha256(b"%PDF receipt").hexdigest()
    config = files(state, "Cash 27", "Config")
    assert json.loads(base64.b64decode(config["voucher-defaults.json"]["content"])) == \
        {"approver_name": "Ana"}

    label = fiscal_label(desk)
    database = files(state, "Cash 27", label, "Database")
    [db_name] = [name for name in database if name.endswith(".db")]
    assert db_name.replace(".db", ".sql") in database
    assert base64.b64decode(database[db_name]["content"])[:16] == b"SQLite format 3\x00"

    log = files(state, "Cash 27")["Sync log"]["id"]
    _, runs = rows(state, log, "Runs")
    assert runs[0][2] == "Result" and runs[1][2] == "OK"
    assert runs[1][4] == 3  # two documents and the settings file
    _, snapshots = rows(state, log, "Snapshots")
    assert [row[6] for row in snapshots[1:]] == ["kept", "kept"]
    assert sync.status(desk.root)["last_completed_at"] is not None

    ledger = files(state, "Cash 27", "FY27")["FY27 Ledger"]["id"]
    sheet, values = rows(state, ledger, "Expense report")
    assert values[0][0].startswith("Generated nightly from Cash 27")
    assert values[1] == ["Date", "Payee/Vendor", "Category", "Class", "Description",
                         "MXN", "ER", "USD", "Calc USD", "Documents"]
    assert values[2][1] == "'=Vendor"  # Text, not a formula.
    assert values[2][7] == 12.5
    documents_cell = sheet["rows"][2][9]
    assert documents_cell["value"] == "Txn-2-receipt"
    assert documents_cell["links"] == [{"start": 0, "end": 13,
                                        "url": f"https://drive.test/file/{receipt['id']}"}]
    assert sheet["frozen"] == 2
    assert sheet["protections"] == [{"warningOnly": True,
                                     "description": "Written by the Cash 27 Drive backup"}]
    assert sheet["formats"]["7"] == "0.00"
    assert [s["name"] for s in state["spreadsheets"][ledger]["sheets"]] == \
        ["Expense report", "Cash ledger"]
    assert state["mails"] == []


def test_quiet_nights_change_nothing_and_new_work_is_copied(desk):
    desk.run("setup", "nightlySync")
    before = desk.saved
    assert desk.run("nightlySync") is None
    state = desk.saved
    log = files(state, "Cash 27")["Sync log"]["id"]
    _, runs = rows(state, log, "Runs")
    assert runs[-1][2:6] == ["OK", "unchanged", 0, 0]
    assert len(files(state, "Cash 27", fiscal_label(desk), "Database")) == 2
    assert state["spreadsheets"] == before["spreadsheets"] | {
        log: state["spreadsheets"][log]}

    desk.document("D000003--late-receipt", b"%PDF late")
    desk.txn(date="2026-09-03", usd_cents=300, description="Brushes")
    assert desk.run("nightlySync") is None
    state = desk.saved
    _, runs = rows(state, log, "Runs")
    assert runs[-1][2] == "OK"
    assert runs[-1][3].startswith("stored cash-27-")
    assert runs[-1][4] == 1
    assert runs[-1][5] == 1  # Only FY27 changed.
    assert "20260902T000000Z--D000003--late-receipt.pdf" in files(state, "Cash 27", "Documents")
    # Counted directly: two runs in one second give snapshots the same name.
    assert len(children(state, folder(state, "Cash 27", fiscal_label(desk), "Database"))) == 4


def test_a_document_deleted_from_drive_is_copied_again(desk):
    desk.run("setup", "nightlySync")
    name = "20260902T000000Z--D000002--inbox.pdf"
    desk.edit(lambda state: files(state, "Cash 27", "Documents")[name].update(trashed=True))
    assert desk.run("nightlySync") is None
    assert name in files(desk.saved, "Cash 27", "Documents")


def test_a_missing_snapshot_is_reported_by_email(desk):
    desk.run("setup", "nightlySync")
    completed = sync.status(desk.root)["last_completed_at"]
    database = files(desk.saved, "Cash 27", fiscal_label(desk), "Database")
    [db_name] = [name for name in database if name.endswith(".db")]
    desk.edit(lambda state: files(state, "Cash 27", fiscal_label(desk), "Database")[db_name]
              .update(trashed=True))
    error = desk.run("nightlySync")
    assert f"Database snapshot {db_name} is missing from Drive" in error
    [mail] = desk.saved["mails"]
    assert mail["to"] == "owner@example.com"
    assert db_name in mail["body"]
    # A run with problems is not reported to Cash 27 as complete.
    assert sync.status(desk.root)["last_completed_at"] == completed
    # It is reported once, not every night.
    assert desk.run("nightlySync") is None


def test_old_snapshots_keep_the_first_of_each_month(desk):
    desk.run("setup", "nightlySync")
    log = files(desk.saved, "Cash 27")["Sync log"]["id"]
    old = datetime.now(timezone.utc) - timedelta(days=90)
    month = old.strftime("%Y%m")

    def add_old(state):
        target = folder(state, "Cash 27", fiscal_label(desk), "Database")
        sheet = [s for s in state["spreadsheets"][log]["sheets"] if s["name"] == "Snapshots"][0]
        for day in ("01", "02"):
            name = f"cash-27-{month}{day}T000000Z.db"
            file_id = f"old{day}"
            state["items"][file_id] = {"id": file_id, "folder": False, "name": name,
                                       "parent": target, "trashed": False, "content": ""}
            sheet["rows"].append([{"value": v} for v in
                                  ("", "FY", name, file_id, "sig", "sha", "kept")])
    desk.edit(add_old)
    assert desk.run("nightlySync") is None
    state = desk.saved
    assert state["items"]["old01"]["trashed"] is False
    assert state["items"]["old02"]["trashed"] is True
    _, snapshots = rows(state, log, "Snapshots")
    assert [row[6] for row in snapshots if row[3] in ("old01", "old02")] == ["kept", "pruned"]


def test_a_revoked_token_fails_loudly(desk):
    desk.run("setup")
    sync.revoke_token(desk.root)
    error = desk.run("nightlySync")
    assert "answered 401" in error
    assert desk.saved["mails"][0]["subject"] == "Cash 27 Drive backup: problems"


def test_setup_needs_the_script_properties(desk):
    desk.edit(lambda state: state.update(props={}))
    assert "Set the CASH_URL and CASH_TOKEN" in desk.run("setup")


def test_changed_settings_replace_the_old_copy(desk):
    desk.run("setup", "nightlySync")
    voucher_defaults.save_default(desk.root, "Luis")
    assert desk.run("nightlySync") is None
    config = [item for item in children(desk.saved, folder(desk.saved, "Cash 27", "Config"))
              if item["name"] == "voucher-defaults.json"]
    assert len(config) == 1
    assert json.loads(base64.b64decode(config[0]["content"])) == {"approver_name": "Luis"}


def test_a_drive_copy_that_differs_is_kept_aside_and_copied_again(desk):
    desk.run("setup", "nightlySync")
    name = "20260902T000000Z--D000002--inbox.pdf"
    desk.edit(lambda state: files(state, "Cash 27", "Documents")[name].update(
        content=base64.b64encode(b"%PDF changed in Drive").decode()))
    error = desk.run("nightlySync")
    assert f"The Drive copy of {name} did not match" in error
    documents = files(desk.saved, "Cash 27", "Documents")
    assert base64.b64decode(documents[name]["content"]) == b"%PDF waiting in the inbox"
    assert base64.b64decode(documents["DIFFERS " + name]["content"]) == b"%PDF changed in Drive"
