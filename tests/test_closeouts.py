"""Confirmed cash settlement and its signed-paperwork lifecycle."""

from datetime import datetime, timedelta, timezone
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from cash import repo, signouts
from cash.db import connect
from cash.web.app import create_app


def sid():
    return str(uuid.uuid4())


@pytest.fixture
def desk(tmp_path):
    app = create_app(tmp_path, admin_password="test", testing=True)
    client = app.test_client()
    csrf = client.post("/api/login", json={"password": "test"}).get_json()["data"]["csrf"]
    conn = connect(Path(app.config["DATA_ROOT"]) / "cash.db")

    def post(url, **body):
        return client.post(url, json={"submission_id": sid(), **body}, headers={"X-CSRF": csrf})

    result = post("/api/entries/opening", date="2026-09-01", usd="1000", mxn="1500",
                  assigned_rate_milli=15000)
    assert result.status_code == 200, result.get_json()
    conn.execute("INSERT INTO classes (class_code, name) VALUES ('GENERAL', 'General')")
    yield conn, client, post
    conn.close()


def data(response):
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]


def document(conn):
    doc = sid()
    conn.execute("INSERT INTO documents (document_id, rel_path, sha256, size_bytes, state)"
                 " VALUES (?, ?, ?, 100, 'inbox')", (doc, f"documents/{doc}.pdf", doc))
    return doc


def voucher(desk, kind="signout", usd="20", mxn="0", signed=True):
    conn, _, post = desk
    prepared = data(post("/api/vouchers/prepare", type=kind, date="2026-09-02",
                         recipient="Ana", purpose="Supplies", usd=usd, mxn=mxn))
    vid = prepared["voucher_id"]
    if signed and kind == "signout":
        data(post(f"/api/review/signout/{vid}/attach-voucher", expected_version=1,
                  document_id=document(conn)))
    return vid


def receipt(desk, vid, mxn=40000, usd_paid=0, mxn_paid=None, change=0, split=False):
    """A report row for a receipt worth `mxn` centavos. Paying USD with MXN
    change records the receipt total."""
    conn, _, post = desk
    if mxn_paid is None:
        mxn_paid = mxn if not usd_paid else 0
    usd, net_mxn = usd_paid, mxn_paid - change
    doc = document(conn)
    conn.execute("UPDATE documents SET voucher_id=?, state='signee_draft' WHERE document_id=?", (vid, doc))
    amount = lambda minor: str(Decimal(minor) / 100)
    mixed = usd > 0 > net_mxn
    fields = {"document_id": doc, "date": "2026-09-03", "vendor": "Store",
              "purpose": "Supplies", "usd": amount(usd), "mxn": amount(net_mxn),
              "receipt_total": amount(mxn) if mixed else ""}
    if split:
        fields["splits"] = [{
            "usd": amount(usd // 2), "mxn": amount(net_mxn // 2),
            "receipt_total": amount(mxn // 2) if mixed else "", "purpose": "Supplies",
            "class_code": "GENERAL", "expense_account": account}
            for account in ("EXP_SUPPLIES", "EXP_PROGRAMS")]
    else:
        fields.update(class_code="GENERAL", expense_account="EXP_SUPPLIES")
    data(post(f"/{vid}/api/rows", **fields))


def approve(desk, vid, **settlement):
    """Approve the report and settle the signout on 2026-09-04. Unless told
    otherwise, cash the report still shows goes back to the safe, and cash it
    shows overspent is reimbursed; a USD-to-MXN exchange needs the amounts."""
    _, client, post = desk
    left = data(client.get(f"/{vid}/api/report"))["balance"]
    usd, mxn = Decimal(left["usd"]), Decimal(left["mxn"])
    if usd >= 0 and mxn >= 0:
        amounts = {"return_usd": left["usd"], "return_mxn": left["mxn"]}
    elif usd <= 0 and mxn <= 0:
        amounts = {"reimburse_usd": str(-usd), "reimburse_mxn": str(-mxn)}
    else:
        amounts = {}
    return post(f"/{vid}/api/report/approve",
                **{"date": "2026-09-04", "confirmed": True, **amounts, **settlement})


@pytest.mark.parametrize("split", [False, True])
def test_dollar_signout_peso_receipt_and_return_are_settled_once(desk, split):
    conn, client, post = desk
    vid = voucher(desk)
    receipt(desk, vid, split=split)
    before = repo.replay_projected(conn)
    assert (before.states[vid].usd, before.states[vid].mxn) == (2000, 0)   # nothing posted yet
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    submission = sid()
    view = data(approve(desk, vid, return_mxn="100", submission_id=submission,
                        approver_name="Settlement Approver"))
    assert data(approve(desk, vid, submission_id=submission)) == view
    # The receipt lines, the exchange, and the returned pesos, all at once.
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == count + (2 if split else 1) + 2
    after = repo.replay_projected(conn)
    assert (after.states[vid].usd, after.states[vid].mxn, after.states[vid].basis) == (0, 0, 0)
    assert not after.open_claims
    assert after.expense_value == 16
    assert conn.execute("SELECT payment_mode FROM receipts").fetchone()[0] == "cash"
    assert after.states["SAFE"].basis == 104
    assert after.external_in == sum(s.value_usd for s in after.states.values()) + after.expense_value
    assert signouts.summary(conn, vid)["reasons"] == ["addendum needed"]
    closeout = conn.execute("SELECT * FROM signout_closeouts").fetchone()
    assert closeout["settlement_txn_id"] not in [r["txn_id"] for r in repo.pending_exchanges(conn)]
    ledger = client.get("/api/ledger").get_json()["data"]["transactions"]
    assert all(not r["incomplete"] for r in ledger if r["txn_type"] == "expense")
    aid = closeout["return_addendum_id"]
    [addendum] = view["paperwork"]["addenda"]
    assert addendum["addendum_id"] == aid and addendum["status"] == "pending"
    assert "Settlement Approver" in client.get(addendum["print_url"]).text
    data(post(f"/api/review/addendum/{aid}/complete", expected_version=1,
              document_id=document(conn)))
    assert signouts.summary(conn, vid)["status"] == "closed"
    assert approve(desk, vid).get_json()["error"]["code"] == "voucher_read_only"


def test_known_vendor_payment_does_not_generate_a_second_exchange(desk):
    conn, _, _ = desk
    vid = voucher(desk)
    receipt(desk, vid, usd_paid=2000, change=10000)
    data(approve(desk, vid))
    after = repo.replay_projected(conn)
    assert after.expense_value == 16
    assert after.states["SAFE"].basis == 104
    assert conn.execute("SELECT settlement_txn_id FROM signout_closeouts").fetchone()[0] is None


def test_existing_peso_stock_keeps_its_basis(desk):
    conn, _, _ = desk
    vid = voucher(desk, mxn="150")
    receipt(desk, vid, mxn=20000)
    receipt(desk, vid, mxn=40000)
    data(approve(desk, vid, return_mxn="50"))
    # Original 150 pesos carry $10. The remaining 450 spent + 50 returned
    # are funded by $20 at 25 MXN/USD: $28 spent and $2 returned.
    exchange = conn.execute("SELECT usd_cents, mxn_centavos FROM transactions"
                            " WHERE txn_type='exchange'").fetchone()
    assert (exchange[0], -exchange[1]) == (2000, 50000)
    assert repo.replay_projected(conn).expense_value == 28


def test_same_currency_settlement_needs_no_exchange(desk):
    conn, _, _ = desk
    vid = voucher(desk, usd="0", mxn="500")
    receipt(desk, vid)
    data(approve(desk, vid))
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE txn_type='exchange'").fetchone()[0] == 0
    assert signouts.summary(conn, vid)["reasons"] == ["addendum needed"]


@pytest.mark.parametrize("returned", ["20", "30"])
def test_unexplained_dollar_balance_rejects_the_whole_approval(desk, returned):
    conn, _, _ = desk
    vid = voucher(desk)
    receipt(desk, vid)
    result = approve(desk, vid, return_usd=returned, return_mxn="0")
    assert result.status_code == 422
    assert result.get_json()["error"]["code"] == "unreconciled_cash"
    assert conn.execute("SELECT COUNT(*) FROM signout_closeouts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


def test_approval_needs_the_administrators_confirmation_and_writes_nothing_without_it(desk):
    conn, _, _ = desk
    vid = voucher(desk)
    receipt(desk, vid)
    result = approve(desk, vid, return_mxn="100", confirmed=False)
    assert result.status_code == 422
    assert result.get_json()["error"]["code"] == "confirmation_required"
    assert conn.execute("SELECT COUNT(*) FROM signout_closeouts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE txn_type IN ('exchange', 'expense')").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM addenda").fetchone()[0] == 0


@pytest.mark.parametrize("problem,code", [
    ("unsigned", "paperwork_needed"), ("date", "closeout_date"),
    ("negative", "bad_return"), ("nan", "bad_amount")])
def test_settlement_preconditions(desk, problem, code):
    vid = voucher(desk, signed=problem != "unsigned")
    receipt(desk, vid)
    result = approve(desk, vid, date="2026-09-01" if problem == "date" else "2026-09-04",
                     return_mxn="-1" if problem == "negative" else "NaN" if problem == "nan" else "100")
    assert result.status_code in (400, 422)
    assert result.get_json()["error"]["code"] == code


def test_later_corrections_do_not_reopen_a_settled_signout(desk):
    conn, _, post = desk
    vid = voucher(desk)
    receipt(desk, vid)
    view = data(approve(desk, vid, return_mxn="100"))
    aid = view["paperwork"]["addenda"][0]["addendum_id"]
    data(post(f"/api/review/addendum/{aid}/complete", expected_version=1,
              document_id=document(conn)))
    assert not signouts.summary(conn, vid)["open"]
    conn.execute("UPDATE transactions SET version=version+1, description='Corrected coding'"
                 " WHERE voucher_id=? AND txn_type='expense'", (vid,))
    assert signouts.summary(conn, vid)["status"] == "closed"


@pytest.mark.parametrize("use_as_receipt", [False, True])
def test_amount_only_direct_voucher_does_not_require_a_receipt(desk, use_as_receipt):
    conn, client, post = desk
    vid = voucher(desk, kind="direct_expense")
    record = conn.execute("SELECT * FROM vouchers WHERE voucher_id=?", (vid,)).fetchone()
    assert record["authorized_usd_cents"] == 2000
    assert not {"face_usd_cents", "face_mxn_centavos", "usd_paid_cents"} & set(record.keys())
    printed = client.get(f"/voucher/{vid}/print").get_data(as_text=True)
    assert "Receipt value" not in printed
    signed = document(conn)
    result = data(post(f"/api/review/direct-expense/{vid}/complete", expected_version=1,
                       voucher_document_id=signed, use_voucher_as_receipt=use_as_receipt,
                       rows=[{"expense_account": "EXP_SUPPLIES", "class_code": "GENERAL",
                              "usd": "20", "mxn": "0", "purpose": "Supplies"}]))
    roles = [r[0] for r in conn.execute("SELECT role FROM transaction_documents ORDER BY role")]
    assert roles == (["support", "voucher"] if use_as_receipt else ["voucher"])
    filed = conn.execute("SELECT state, doc_type FROM documents WHERE document_id=?",
                         (signed,)).fetchone()
    assert tuple(filed) == ("filed", "voucher")
    assert len(result["txn_ids"]) == 1
    assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


@pytest.mark.parametrize("face,expected", [("400", 200), ("0", 422)])
def test_direct_peso_change_requires_purchase_value_and_gets_correct_basis(desk, face, expected):
    conn, _, post = desk
    vid = voucher(desk, kind="direct_expense")
    result = post(f"/api/review/direct-expense/{vid}/complete", expected_version=1,
                  voucher_document_id=document(conn), rows=[{
                      "expense_account": "EXP_SUPPLIES", "class_code": "GENERAL",
                      "usd": "20", "mxn": "-100", "receipt_total": face}])
    assert result.status_code == expected, result.get_json()
    if expected == 200:
        replayed = repo.replay_projected(conn)
        assert replayed.states["SAFE"].basis == 104
        assert replayed.expense_value == 16
    else:
        assert result.get_json()["error"]["code"] == "purchase_value_required"


def test_receipt_review_date_is_the_device_date(desk):
    """Evening in California is already tomorrow in UTC; the receipt keeps
    the reviewer's date. A missing or implausible date falls back to UTC."""
    conn, client, _ = desk
    csrf = client.get("/api/session").get_json()["data"]["csrf"]
    utc_today = datetime.now(timezone.utc).date()
    device_day = (utc_today - timedelta(days=1)).isoformat()
    for header, expected in [(device_day, device_day),
                             ("2001-01-01", utc_today.isoformat()),
                             ("not a date", utc_today.isoformat())]:
        vid = voucher(desk, kind="direct_expense")
        result = client.post(
            f"/api/review/direct-expense/{vid}/complete",
            json={"submission_id": sid(), "expected_version": 1,
                  "voucher_document_id": document(conn),
                  "receipt_document_ids": [document(conn)],
                  "rows": [{"usd": "20", "expense_account": "EXP_SUPPLIES",
                            "class_code": "GENERAL"}]},
            headers={"X-CSRF": csrf, "X-Client-Date": header})
        [receipt_id] = data(result)["receipt_ids"]
        assert conn.execute("SELECT reviewed_date FROM receipts WHERE receipt_id=?",
                            (receipt_id,)).fetchone()[0] == expected
    # A separate receipt does not need its own purchase value.
    assert repo.replay_projected(conn).expense_value == 60


def test_approved_signout_receipt_uses_the_device_date(desk):
    conn, client, post = desk
    vid = voucher(desk)
    receipt(desk, vid)
    device_day = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    csrf = client.get("/api/session").get_json()["data"]["csrf"]
    data(client.post(f"/{vid}/api/report/approve", json={
        "submission_id": sid(), "date": "2026-09-04", "confirmed": True, "return_mxn": "100"},
        headers={"X-CSRF": csrf, "X-Client-Date": device_day}))
    assert conn.execute("SELECT reviewed_date FROM receipts WHERE voucher_id=?",
                        (vid,)).fetchone()[0] == device_day
