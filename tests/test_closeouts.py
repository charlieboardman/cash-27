"""Confirmed cash settlement and its signed-paperwork lifecycle."""

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


def receipt(desk, vid, mxn=40000, usd_paid=0, mxn_paid=None, change=0,
            accept=True, split=False):
    """A report row for a receipt worth `mxn` centavos, approved unless
    accept=False. Paying USD with MXN change records the receipt total."""
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
    if accept:
        return data(post(f"/{vid}/api/report/approve"))


def preview(desk, vid, usd="0", mxn="100"):
    return data(desk[2](f"/api/signouts/{vid}/closeout/preview", date="2026-09-04",
                        return_usd=usd, return_mxn=mxn))


def confirm(desk, vid, reviewed, **overrides):
    body = {"date": reviewed["date"], "confirmed": True,
            "return_usd": reviewed["return_usd"], "return_mxn": reviewed["return_mxn"],
            "preview_token": reviewed["preview_token"], **overrides}
    return desk[2](f"/api/signouts/{vid}/closeout", **body)


@pytest.mark.parametrize("split", [False, True])
def test_dollar_signout_peso_receipt_and_return_are_settled_once(desk, split):
    conn, client, post = desk
    vid = voucher(desk)
    receipt(desk, vid, split=split)
    before = repo.replay_projected(conn)
    assert (before.states[vid].usd, before.states[vid].mxn) == (2000, -40000)
    assert before.open_claims
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    reviewed = preview(desk, vid)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == count
    assert conn.execute("SELECT COUNT(*) FROM signout_closeouts").fetchone()[0] == 0
    assert reviewed["rate_mxn_per_usd"] == "25"
    assert Decimal(reviewed["expense_value_usd"]) == 16
    assert Decimal(reviewed["returned_mxn_basis_usd"]) == 4
    submission = sid()
    result = data(confirm(desk, vid, reviewed, submission_id=submission, approver_name="Settlement Approver"))
    assert data(confirm(desk, vid, reviewed, submission_id=submission)) == result
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == count + 2
    after = repo.replay_projected(conn)
    assert (after.states[vid].usd, after.states[vid].mxn, after.states[vid].basis) == (0, 0, 0)
    assert not after.open_claims
    assert after.expense_value == 16
    assert conn.execute("SELECT payment_mode FROM receipts").fetchone()[0] == "cash"
    assert after.states["SAFE"].basis == 104
    assert after.external_in == sum(s.value_usd for s in after.states.values()) + after.expense_value
    assert result["summary"]["reasons"] == ["addendum needed"]
    assert result["summary"]["closeout"]["valid"]
    closeout = conn.execute("SELECT * FROM signout_closeouts").fetchone()
    assert closeout["settlement_txn_id"] not in [r["txn_id"] for r in repo.pending_exchanges(conn)]
    ledger = client.get("/api/ledger").get_json()["data"]["transactions"]
    assert all(not r["incomplete"] for r in ledger if r["txn_type"] == "expense")
    aid = closeout["return_addendum_id"]
    assert "Settlement Approver" in client.get(result["print_url"]).text
    assert result["print_url"].endswith(f"/{aid}/print")
    data(post(f"/api/review/addendum/{aid}/complete", expected_version=1,
              document_id=document(conn)))
    assert signouts.summary(conn, vid)["status"] == "closed"
    assert signouts.summary(conn, vid)["closeout"]["valid"]
    duplicate = confirm(desk, vid, reviewed)
    assert duplicate.get_json()["error"]["code"] == "already_confirmed"


def test_known_vendor_payment_does_not_generate_a_second_exchange(desk):
    conn, _, _ = desk
    vid = voucher(desk)
    receipt(desk, vid, usd_paid=2000, change=10000)
    reviewed = preview(desk, vid)
    assert reviewed["exchange_usd"] == "0.00"
    assert reviewed["rate_mxn_per_usd"] is None
    assert Decimal(reviewed["expense_value_usd"]) == 16
    assert Decimal(reviewed["returned_mxn_basis_usd"]) == 4
    data(confirm(desk, vid, reviewed))
    assert conn.execute("SELECT settlement_txn_id FROM signout_closeouts").fetchone()[0] is None


def test_existing_peso_stock_keeps_its_basis(desk):
    vid = voucher(desk, mxn="150")
    receipt(desk, vid, mxn=20000)
    receipt(desk, vid, mxn=40000)
    reviewed = preview(desk, vid, mxn="50")
    # Original 150 pesos carry $10. The remaining 450 spent + 50 returned
    # are funded by $20 at 25 MXN/USD: $28 spent and $2 returned.
    assert reviewed["rate_mxn_per_usd"] == "25"
    assert Decimal(reviewed["expense_value_usd"]) == 28
    assert Decimal(reviewed["returned_mxn_basis_usd"]) == 2
    data(confirm(desk, vid, reviewed))


def test_same_currency_closeout_requires_explicit_confirmation(desk):
    conn, _, _ = desk
    vid = voucher(desk, usd="0", mxn="500")
    receipt(desk, vid)
    reviewed = preview(desk, vid)
    assert reviewed["exchange_usd"] == "0.00"
    assert abs(Decimal(reviewed["returned_mxn_basis_usd"]) - Decimal(100) / 15) < Decimal("1e-24")
    data(confirm(desk, vid, reviewed))
    assert signouts.summary(conn, vid)["reasons"] == ["addendum needed"]


@pytest.mark.parametrize("returned", ["0", "30"])
def test_unexplained_dollar_balance_is_rejected_without_writes(desk, returned):
    conn, _, post = desk
    vid = voucher(desk)
    result = post(f"/api/signouts/{vid}/closeout/preview", date="2026-09-04",
                  return_usd=returned, return_mxn="0")
    assert result.status_code == 422
    assert result.get_json()["error"]["code"] == "unreconciled_cash"
    assert conn.execute("SELECT COUNT(*) FROM signout_closeouts").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["amount", "ledger", "facts", "confirmation"])
def test_confirmation_rejects_unreviewed_changes_atomically(desk, change):
    conn, _, post = desk
    vid = voucher(desk)
    receipt(desk, vid)
    reviewed = preview(desk, vid)
    overrides = {}
    if change == "amount":
        overrides["return_mxn"] = "150"
    elif change == "ledger":
        data(post("/api/entries/donation", date="2026-09-04", usd="10", mxn="0"))
    elif change == "facts":
        conn.execute("UPDATE vouchers SET purpose='Corrected' WHERE voucher_id=?", (vid,))
    else:
        overrides["confirmed"] = False
    result = confirm(desk, vid, reviewed, **overrides)
    assert result.status_code == 422
    assert result.get_json()["error"]["code"] == (
        "confirmation_required" if change == "confirmation" else "closeout_changed")
    assert conn.execute("SELECT COUNT(*) FROM signout_closeouts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM transactions WHERE txn_type='exchange'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM addenda").fetchone()[0] == 0


@pytest.mark.parametrize("problem,code", [
    ("unsigned", "paperwork_needed"), ("draft", "receipts_in_progress"),
    ("date", "closeout_date"), ("negative", "bad_return"), ("nan", "bad_amount")])
def test_closeout_preconditions(desk, problem, code):
    _, _, post = desk
    vid = voucher(desk, signed=problem != "unsigned")
    receipt(desk, vid, accept=problem != "draft")
    result = post(f"/api/signouts/{vid}/closeout/preview",
                  date="2026-09-01" if problem == "date" else "2026-09-04",
                  return_mxn="-1" if problem == "negative" else "NaN" if problem == "nan" else "100")
    assert result.status_code in (400, 422)
    assert result.get_json()["error"]["code"] == code


def test_later_history_changes_require_new_confirmation(desk):
    conn, _, post = desk
    vid = voucher(desk)
    receipt(desk, vid)
    result = data(confirm(desk, vid, preview(desk, vid)))
    aid = result["summary"]["closeout"]["return_addendum_id"]
    data(post(f"/api/review/addendum/{aid}/complete", expected_version=1,
              document_id=document(conn)))
    assert not signouts.summary(conn, vid)["open"]
    conn.execute("UPDATE transactions SET version=version+1, description='Corrected coding'"
                 " WHERE voucher_id=? AND txn_type='expense'", (vid,))
    state = signouts.summary(conn, vid)
    assert state["reasons"] == ["closeout needs review"]
    reviewed = preview(desk, vid, mxn="0")
    assert not reviewed["requires_return_addendum"]
    assert reviewed["exchange_usd"] == "0.00"
    result = data(confirm(desk, vid, reviewed))
    assert result["summary"]["status"] == "closed"
    assert conn.execute("SELECT COUNT(*) FROM signout_closeouts").fetchone()[0] == 2


@pytest.mark.parametrize("use_as_receipt", [False, True])
def test_amount_only_direct_voucher_does_not_require_a_receipt(desk, use_as_receipt):
    conn, client, post = desk
    vid = voucher(desk, kind="direct_expense")
    record = conn.execute("SELECT * FROM vouchers WHERE voucher_id=?", (vid,)).fetchone()
    assert record["authorized_usd_cents"] == 2000
    assert not {"face_usd_cents", "face_mxn_centavos", "usd_paid_cents"} & set(record.keys())
    printed = client.get(f"/voucher/{vid}/print").get_data(as_text=True)
    assert "Receipt value" not in printed
    result = data(post(f"/api/review/direct-expense/{vid}/complete", expected_version=1,
                       voucher_document_id=document(conn), use_voucher_as_receipt=use_as_receipt,
                       rows=[{"expense_account": "EXP_SUPPLIES", "class_code": "GENERAL",
                              "usd": "20", "mxn": "0", "purpose": "Supplies"}]))
    roles = [r[0] for r in conn.execute("SELECT role FROM transaction_documents ORDER BY role")]
    assert roles == (["support", "voucher"] if use_as_receipt else ["voucher"])
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


def test_separate_direct_receipt_does_not_require_duplicate_value(desk):
    conn, _, post = desk
    vid = voucher(desk, kind="direct_expense")
    result = data(post(f"/api/review/direct-expense/{vid}/complete", expected_version=1,
                       voucher_document_id=document(conn), rows=[{
                           "document_id": document(conn), "date": "2026-09-02", "usd": "20",
                           "expense_account": "EXP_SUPPLIES", "class_code": "GENERAL"}]))
    assert len(result["receipts"]) == 1
    assert repo.replay_projected(conn).expense_value == 20
