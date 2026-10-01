"""The Drive backup script (gas/Code.gs) against the real Cash 27 routes, with
Google's services replaced by tests/gas_harness.js."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from cash import sync, voucher_defaults
from cash.db import connect
from cash.web.app import create_app

HARNESS = Path(__file__).with_name("gas_harness.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs node")


class Desk:
    """A Cash 27 app with a little ledger, and the script's saved state."""

    def __init__(self, root: Path, state: Path):
        self.root, self.state = root, state
        self.app = create_app(root, admin_password="x", testing=True)
        self.token = sync.set_token(root)
        state.write_text(json.dumps({"httpRoot": str(root), "python": sys.executable, "props": {
            "CASH_URL": "http://cash.test/",
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
        did = label.split("--")[0]
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
    assert set(documents) == {"Txn-2-receipt.pdf", "D000002.pdf"}
    receipt = documents["Txn-2-receipt.pdf"]
    assert base64.b64decode(receipt["content"]) == b"%PDF receipt"
    assert receipt["description"] == "sha256 " + hashlib.sha256(b"%PDF receipt").hexdigest()
    config = files(state, "Cash 27", "Config")
    assert json.loads(base64.b64decode(config["voucher-defaults.json"]["content"])) == \
        {"approver_name": "Ana"}

    label = fiscal_label(desk)
    database = files(state, "Cash 27", label, "Database")
    [db_name] = [name for name in database if name.endswith(".db")]
    assert sum(name.endswith(".sql") for name in database) == 1
    assert base64.b64decode(database[db_name]["content"])[:16] == b"SQLite format 3\x00"

    log = files(state, "Cash 27")["Sync log"]["id"]
    _, runs = rows(state, log, "Runs")
    assert runs[0][2] == "Result" and runs[1][2] == "OK"
    assert runs[1][4] == 4  # two documents, the print settings, and the token
    token = files(state, "Cash 27", "Config", "sync")["token.json"]
    assert json.loads(base64.b64decode(token["content"]))["token"] == desk.token
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
    assert len(state["props"]["SHEET_2027_KEY"]) == 64


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
    assert "D000003.pdf" in files(state, "Cash 27", "Documents")
    # Counted directly: two runs in one second give snapshots the same name.
    assert len(children(state, folder(state, "Cash 27", fiscal_label(desk), "Database"))) == 4


def test_a_document_deleted_from_drive_is_copied_again(desk):
    desk.run("setup", "nightlySync")
    name = "D000002.pdf"
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
    name = "D000002.pdf"
    desk.edit(lambda state: files(state, "Cash 27", "Documents")[name].update(
        content=base64.b64encode(b"%PDF changed in Drive").decode()))
    error = desk.run("nightlySync")
    immutable = "20260902T000000Z--D000002--inbox.pdf"
    assert f"The Drive copy of {immutable} did not match" in error
    documents = files(desk.saved, "Cash 27", "Documents")
    assert base64.b64decode(documents[name]["content"]) == b"%PDF waiting in the inbox"
    assert base64.b64decode(documents["DIFFERS " + immutable]["content"]) == b"%PDF changed in Drive"


def drive_zips(state, target: Path, parts: int = 1) -> list[Path]:
    """The "Cash 27" folder as Drive downloads it: zip files of its live
    contents, Sheets converted to .xlsx, large folders split across zips."""
    entries = []

    def walk(parent, prefix):
        for item in children(state, parent):
            path = f"{prefix}/{item['name']}"
            if item["folder"]:
                walk(item["id"], path)
            elif item["id"] in state["spreadsheets"]:
                entries.append((path + ".xlsx", b"PK converted sheet"))
            elif item.get("mime") != "application/vnd.google-apps.shortcut":
                # Restoration must work without any shortcut export metadata.
                entries.append((path, base64.b64decode(item["content"])))
    walk(folder(state, "Cash 27"), "Cash 27")
    from zipfile import ZipFile
    zips = [target / f"drive-download-{n}.zip" for n in range(parts)]
    handles = [ZipFile(path, "w") for path in zips]
    for n, (name, content) in enumerate(sorted(entries)):
        handles[n % parts].writestr(name, content)
    for handle in handles:
        handle.close()
    return zips


def test_restore_from_the_drive_download_rebuilds_cash_27(desk, tmp_path):
    from cash.drive_restore import build_archive
    from cash.ops import restore_archive
    desk.run("setup", "nightlySync")
    zips = drive_zips(desk.saved, tmp_path, parts=2)
    archive = tmp_path / "data2" / "backups" / "restore" / "cash-27-full-20260927T120000Z.tar.gz"
    result = build_archive(zips, archive)
    assert result["integrity"] == "ok"
    assert result["documents"] == 2 and result["config_files"] == 2

    fresh = tmp_path / "data2"
    restore_archive(fresh, archive)
    for name in ("20260902T000000Z--D000001--receipt.pdf", "20260902T000000Z--D000002--inbox.pdf"):
        assert (fresh / "documents" / name).read_bytes() == (desk.root / "documents" / name).read_bytes()
    assert json.loads((fresh / "config" / "voucher-defaults.json").read_text()) == \
        {"approver_name": "Ana"}
    # The script keeps working with the restored server.
    assert sync.token_valid(fresh, desk.token)
    restored = connect(fresh / "cash.db")
    try:
        query = "SELECT txn_id, date, usd_cents, vendor FROM transactions ORDER BY txn_id"
        assert restored.execute(query).fetchall() == desk.conn.execute(query).fetchall()
    finally:
        restored.close()


def test_restore_picks_a_named_snapshot_and_refuses_an_incomplete_download(desk, tmp_path):
    from cash.drive_restore import DriveRestoreError, build_archive
    desk.run("setup", "nightlySync")
    first = [n for n in files(desk.saved, "Cash 27", fiscal_label(desk), "Database") if n.endswith(".db")][0]
    desk.document("D000003--later", b"%PDF later")
    zips = drive_zips(desk.saved, tmp_path)
    archive = tmp_path / "out" / "cash-27-full-20260927T120000Z.tar.gz"
    assert build_archive(zips, archive, database=first)["database"] == first
    with pytest.raises(DriveRestoreError, match="not in the zip files"):
        build_archive(zips, archive, database="cash-27-20000101T000000Z.db")

    # Without a document the snapshot names, nothing is produced.
    state = desk.saved
    files(state, "Cash 27", "Documents")["D000002.pdf"]["trashed"] = True
    incomplete = drive_zips(state, tmp_path / "out")
    archive.unlink()
    with pytest.raises(DriveRestoreError, match="D000002--inbox.pdf"):
        build_archive(incomplete, archive)
    assert not archive.exists()


def test_the_restore_tool_command_builds_the_archive(desk, tmp_path):
    import sys
    desk.run("setup", "nightlySync")
    zips = drive_zips(desk.saved, tmp_path)
    archive = tmp_path / "out" / "cash-27-full-20260927T120000000000Z.tar.gz"
    done = subprocess.run([sys.executable, "-m", "cash.ops", "drive-archive", str(archive),
                           *map(str, zips)], capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parents[1])
    assert done.returncode == 0, done.stderr
    assert "'integrity': 'ok'" in done.stdout
    assert archive.is_file()


def test_drive_is_listed_in_bulk_with_no_per_file_lookups(desk):
    for n in range(3, 8):
        desk.document(f"D00000{n}--more", f"%PDF {n}".encode())
    desk.run("setup", "nightlySync")
    # Page by page, and no per-file size or link lookups on a quiet night.
    desk.edit(lambda state: state.update(drivePageSize=2, calls={}))
    assert desk.run("nightlySync") is None
    state = desk.saved
    assert state["calls"]["driveList"] == 9  # PDFs, shortcuts, and the empty Originals folder
    assert "getSize" not in state["calls"] and "getUrl" not in state["calls"]
    log = files(state, "Cash 27")["Sync log"]["id"]
    _, runs = rows(state, log, "Runs")
    assert runs[-1][2:6] == ["OK", "unchanged", 0, 0]

    # A document deleted from Drive is still noticed and copied again.
    name = "D000005.pdf"
    desk.edit(lambda state: files(state, "Cash 27", "Documents")[name].update(trashed=True))
    assert desk.run("nightlySync") is None
    assert name in files(desk.saved, "Cash 27", "Documents")


def test_listing_falls_back_to_drive_app_when_the_api_is_refused(desk):
    desk.edit(lambda state: state.update(driveApiDisabled=True))
    assert desk.run("setup", "nightlySync") is None
    state = desk.saved
    assert any("file by file" in line for line in state["logs"])
    receipt = files(state, "Cash 27", "Documents")["Txn-2-receipt.pdf"]
    ledger = files(state, "Cash 27", "FY27")["FY27 Ledger"]["id"]
    sheet, _ = rows(state, ledger, "Expense report")
    assert sheet["rows"][2][9]["links"][0]["url"] == f"https://drive.test/file/{receipt['id']}"
    assert desk.run("nightlySync") is None
    assert len(files(desk.saved, "Cash 27", "Documents")) == 2


def test_adopts_existing_pdfs_without_reupload_or_changing_ids(desk):
    desk.run('setup')
    def seed(state):
        parent = folder(state, 'Cash 27', 'Documents')
        for row in desk.conn.execute('SELECT * FROM documents'):
            key = 'legacy-' + row['document_id']
            state['items'][key] = dict(id=key, folder=False, parent=parent,
                trashed=False, name=Path(row['rel_path']).name,
                content=base64.b64encode((desk.root / row['rel_path']).read_bytes()).decode(),
                description='sha256 ' + row['sha256'], mime='application/pdf')
    desk.edit(seed)
    assert desk.run('nightlySync') is None
    state = desk.saved
    documents = files(state, 'Cash 27', 'Documents')
    assert documents['Txn-2-receipt.pdf']['id'] == 'legacy-D000001'
    assert documents['D000002.pdf']['id'] == 'legacy-D000002'
    shortcuts = files(state, 'Cash 27', 'Object links')
    assert len(shortcuts) == 2
    assert shortcuts['20260902T000000Z--D000001--receipt.pdf']['targetId'] == 'legacy-D000001'
    assert desk.run('nightlySync') is None
    assert len(files(desk.saved, 'Cash 27', 'Documents')) == 2


def test_reference_changes_rename_same_drive_file_and_keep_shortcut(desk):
    desk.run('setup', 'nightlySync')
    before = desk.saved
    original = files(before, 'Cash 27', 'Documents')['Txn-2-receipt.pdf']
    shortcuts = files(before, 'Cash 27', 'Object links')
    # A filing changes the canonical reference, without replacing the PDF.
    desk.conn.execute("INSERT INTO vouchers(voucher_id,type,fiscal_year,seq)"
                      " VALUES ('FY27-0001','direct_expense',2027,1)")
    desk.conn.execute("INSERT INTO receipts(receipt_id,voucher_id,seq,document_id)"
                      " VALUES ('FY27-0001-R1','FY27-0001',1,'D000001')")
    assert desk.run('nightlySync') is None
    renamed = files(desk.saved, 'Cash 27', 'Documents')['FY27-0001-R1.pdf']
    assert renamed['id'] == original['id'] and renamed['content'] == original['content']
    assert files(desk.saved, 'Cash 27', 'Object links') == shortcuts


def test_interrupted_adoption_resumes_and_repairs_deleted_shortcut(desk):
    desk.run('setup')
    desk.edit(lambda state: state.update(failShortcutOnce=True))
    assert 'Interrupted shortcut creation' in desk.run('nightlySync')
    pdfs = files(desk.saved, 'Cash 27', 'Documents')
    original_ids = {item['id'] for item in pdfs.values()}
    assert desk.run('nightlySync') is None
    assert {item['id'] for item in files(desk.saved, 'Cash 27', 'Documents').values()} == original_ids
    def lose_link(state):
        link = files(state, 'Cash 27', 'Object links')['20260902T000000Z--D000001--receipt.pdf']
        link['trashed'] = True
    desk.edit(lose_link)
    assert desk.run('nightlySync') is None
    assert {item['id'] for item in files(desk.saved, 'Cash 27', 'Documents').values()} == original_ids


def test_replaced_target_refreshes_sheet_link_even_with_same_count(desk):
    desk.run('setup', 'nightlySync')
    previous = files(desk.saved, 'Cash 27', 'Documents')['Txn-2-receipt.pdf']['id']
    desk.edit(lambda state: state['items'].pop(previous))
    assert desk.run('nightlySync') is None
    state = desk.saved
    replacement = files(state, 'Cash 27', 'Documents')['Txn-2-receipt.pdf']['id']
    assert replacement != previous
    ledger = files(state, 'Cash 27', 'FY27')['FY27 Ledger']['id']
    sheet, _ = rows(state, ledger, 'Expense report')
    assert sheet['rows'][2][9]['links'][0]['url'] == f'https://drive.test/file/{replacement}'
    link = files(state, 'Cash 27', 'Object links')['20260902T000000Z--D000001--receipt.pdf']
    assert link['targetId'] == replacement


def test_distinct_documents_with_identical_bytes_get_distinct_targets(desk):
    did = desk.document('D000003--same-bytes', b'%PDF receipt')
    tid = desk.txn(date='2026-09-03', usd_cents=100, description='Other purchase')
    desk.conn.execute("INSERT INTO transaction_documents(txn_id,document_id,role) VALUES (?,?,'support')", (tid, did))
    assert desk.run('setup', 'nightlySync') is None
    state = desk.saved
    docs = files(state, 'Cash 27', 'Documents')
    assert docs['Txn-2-receipt.pdf']['id'] != docs[f'Txn-{tid}-receipt.pdf']['id']
    assert docs['Txn-2-receipt.pdf']['content'] == docs[f'Txn-{tid}-receipt.pdf']['content']
    assert len(files(state, 'Cash 27', 'Object links')) == 3
    ledger = files(state, 'Cash 27', 'FY27')['FY27 Ledger']['id']
    sheet, _ = rows(state, ledger, 'Expense report')
    assert sheet['rows'][2][9]['links'][0]['url'] != sheet['rows'][3][9]['links'][0]['url']
    assert desk.run('nightlySync') is None
    assert len(files(desk.saved, 'Cash 27', 'Documents')) == 3


def test_originals_are_separate_and_restore_with_current_pdfs(desk, tmp_path):
    from cash.drive_restore import build_archive
    source = desk.root / 'documents' / 'original-upload.pdf'
    source.write_bytes(b'%PDF combined original')
    desk.conn.execute("INSERT INTO document_processing(document_id,status,source_rel_path,source_sha256,captured_at)"
                      " VALUES ('D000001','complete',?,?, '')",
                      ('documents/' + source.name, hashlib.sha256(source.read_bytes()).hexdigest()))
    assert desk.run('setup', 'nightlySync') is None
    state = desk.saved
    assert set(files(state, 'Cash 27', 'Originals')) == {'original-upload.pdf'}
    assert len(files(state, 'Cash 27', 'Documents')) == 2
    archive = tmp_path / 'backups' / 'cash-27-full-20261001T120000Z.tar.gz'
    assert build_archive(drive_zips(state, tmp_path), archive)['documents'] == 3


def test_ambiguous_shortcuts_are_reported_without_renaming_or_upload(desk):
    desk.run('setup', 'nightlySync')
    def duplicate(state):
        link = next(iter(files(state, 'Cash 27', 'Object links').values()))
        state['items']['duplicate-link'] = dict(link, id='duplicate-link')
    desk.edit(duplicate)
    before = files(desk.saved, 'Cash 27', 'Documents')
    assert 'Ambiguous immutable shortcuts' in desk.run('nightlySync')
    assert files(desk.saved, 'Cash 27', 'Documents') == before


def test_canonical_name_collision_preserves_both_objects(desk):
    tid = desk.conn.execute('SELECT MAX(txn_id) FROM transactions').fetchone()[0]
    desk.conn.execute("INSERT INTO transaction_documents(txn_id,document_id,role)"
                      " VALUES (?,'D000002','support')", (tid,))
    assert 'Conflicting canonical filename' in desk.run('setup', 'nightlySync')
    assert set(files(desk.saved, 'Cash 27', 'Documents')) == {
        '20260902T000000Z--D000001--receipt.pdf', '20260902T000000Z--D000002--inbox.pdf'}
    assert len(files(desk.saved, 'Cash 27', 'Object links')) == 2


def test_one_target_cannot_represent_two_different_immutable_objects(desk):
    desk.run('setup', 'nightlySync')
    def cross_link(state):
        links = list(files(state, 'Cash 27', 'Object links').values())
        links[1]['targetId'] = links[0]['targetId']
    desk.edit(cross_link)
    before = files(desk.saved, 'Cash 27', 'Documents')
    assert 'different immutable object names' in desk.run('nightlySync')
    assert files(desk.saved, 'Cash 27', 'Documents') == before


def test_ambiguous_legacy_files_are_not_adopted(desk):
    desk.run('setup', 'nightlySync')
    def duplicate(state):
        name = '20260902T000000Z--D000001--receipt.pdf'
        files(state, 'Cash 27', 'Object links')[name]['trashed'] = True
        target = files(state, 'Cash 27', 'Documents')['Txn-2-receipt.pdf']
        target['name'] = name
        state['items']['other-copy'] = dict(target, id='other-copy')
    desk.edit(duplicate)
    before = {k: v for k, v in desk.saved['items'].items() if not v['folder']}
    assert 'Ambiguous existing PDFs' in desk.run('nightlySync')
    for key, original in before.items():
        assert desk.saved['items'][key] == original


def test_pdfs_required_only_by_older_snapshots_are_retained(desk):
    desk.run('setup', 'nightlySync')
    original = files(desk.saved, 'Cash 27', 'Documents')['D000002.pdf']
    desk.conn.execute("DELETE FROM documents WHERE document_id='D000002'")
    assert desk.run('nightlySync') is None
    assert files(desk.saved, 'Cash 27', 'Documents')['D000002.pdf'] == original


def test_older_server_cannot_trigger_document_reconciliation(desk):
    desk.run('setup', 'nightlySync')
    before = desk.saved
    desk.edit(lambda state: state.update(legacyManifest=True))
    assert 'document naming metadata is missing' in desk.run('nightlySync')
    for directory in ['Documents', 'Originals', 'Object links']:
        assert files(desk.saved, 'Cash 27', directory) == files(before, 'Cash 27', directory)


def test_new_identical_object_does_not_steal_a_legacy_objects_identity(desk):
    desk.run('setup', 'nightlySync')
    previous_id = files(desk.saved, 'Cash 27', 'Documents')['Txn-2-receipt.pdf']['id']
    def revert_to_legacy(state):
        for link in files(state, 'Cash 27', 'Object links').values():
            state['items'][link['targetId']]['name'] = link['name']
            link['trashed'] = True
    desk.edit(revert_to_legacy)
    # This missing object sorts ahead of the legacy file with identical bytes.
    desk.document('D000000--new-first', b'%PDF receipt')
    assert desk.run('nightlySync') is None
    docs = files(desk.saved, 'Cash 27', 'Documents')
    assert docs['Txn-2-receipt.pdf']['id'] == previous_id
    assert docs['D000000.pdf']['id'] != previous_id
