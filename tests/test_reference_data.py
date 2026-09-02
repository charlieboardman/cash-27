"""CSV-backed account and class reference data."""

from pathlib import Path

import pytest

from cash.db import apply_migrations, connect
from cash.reference_data import ReferenceDataError, import_reference_data


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def connection(tmp_path):
    opened = connect(tmp_path / "cash.db")
    apply_migrations(opened, MIGRATIONS)
    yield opened
    opened.close()


def csv_file(tmp_path, name, contents):
    path = tmp_path / name
    path.write_text(contents, encoding="utf-8")
    return path


def test_reference_csvs_add_refresh_and_reactivate_codes(connection, tmp_path):
    accounts = csv_file(
        tmp_path, "accounts.csv",
        "name,code\nTravel,exp_travel\nProgram supplies,EXP_PROGRAM\n",
    )
    classes = csv_file(
        tmp_path, "classes.csv",
        "name,code\nProgram services,program\nAdministration,ADMIN\n",
    )

    result = import_reference_data(
        connection, accounts_csv=accounts, classes_csv=classes)
    assert result == {"accounts": 2, "classes": 2}
    connection.execute(
        "UPDATE accounts SET active=0 WHERE account_id='EXP_TRAVEL'")
    accounts.write_text(
        "name,code\nTravel and lodging,EXP_TRAVEL\n", encoding="utf-8")
    import_reference_data(
        connection, accounts_csv=accounts, classes_csv=classes)

    account = connection.execute(
        "SELECT name, kind, active FROM accounts WHERE account_id='EXP_TRAVEL'"
    ).fetchone()
    assert dict(account) == {
        "name": "Travel and lodging", "kind": "expense", "active": 1}
    assert connection.execute(
        "SELECT name FROM classes WHERE class_code='PROGRAM'"
    ).fetchone()[0] == "Program services"
    assert connection.execute(
        "SELECT 1 FROM accounts WHERE account_id='EXP_PROGRAM'"
    ).fetchone()


def test_reference_csv_import_is_atomic_and_rejects_reserved_code(
        connection, tmp_path):
    accounts = csv_file(
        tmp_path, "accounts.csv",
        "name,code\nTravel,EXP_TRAVEL\nNot a safe,SAFE\n",
    )
    classes = csv_file(
        tmp_path, "classes.csv", "name,code\nProgram,PROGRAM\n")

    with pytest.raises(ReferenceDataError, match="reserved"):
        import_reference_data(
            connection, accounts_csv=accounts, classes_csv=classes)
    assert not connection.execute(
        "SELECT 1 FROM accounts WHERE account_id='EXP_TRAVEL'").fetchone()
    assert not connection.execute(
        "SELECT 1 FROM classes WHERE class_code='PROGRAM'").fetchone()


def test_reference_csv_requires_exact_name_code_headers(connection, tmp_path):
    accounts = csv_file(
        tmp_path, "accounts.csv", "code,name\nEXP_TRAVEL,Travel\n")
    classes = csv_file(tmp_path, "classes.csv", "name,code\n")

    with pytest.raises(ReferenceDataError, match="name,code"):
        import_reference_data(
            connection, accounts_csv=accounts, classes_csv=classes)
