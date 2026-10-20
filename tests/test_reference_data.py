"""CSV-backed account and class reference data."""

from pathlib import Path
import subprocess
import sys

import pytest

from cash.db import apply_migrations, connect
from cash.reference_data import ReferenceDataError, import_reference_data, normalize_code


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


def test_numeric_codes_and_leading_zeros_survive_csv_import(connection, tmp_path):
    accounts = csv_file(tmp_path, "accounts.csv",
                        "name,code\nSupplies,1000\nTravel,0010\nOther,0\n")
    classes = csv_file(tmp_path, "classes.csv",
                       "name,code\nProgram,1\nAdministration,001\n")
    assert import_reference_data(connection, accounts_csv=accounts,
                                 classes_csv=classes) == {"accounts": 3, "classes": 2}
    for code in ("1000", "0010", "0"):
        row = connection.execute(
            "SELECT account_id, kind FROM accounts WHERE account_id=?", (code,)).fetchone()
        assert tuple(row) == (code, "expense")
    assert {row[0] for row in connection.execute("SELECT class_code FROM classes")} == {"1", "001"}


def test_dotted_codes_remain_distinct_text_identifiers(connection, tmp_path):
    accounts = csv_file(
        tmp_path, "accounts.csv",
        'name,code\n"Medical-General, Not Psychology",5170\n'
        "Account one,4.1\nAccount two,4.10\nAccount three,004.10\n",
    )
    class_codes = ("7.5", "4.2", "4.3", "4.7", "4.8", "4.1", "4.10", "004.10")
    classes = csv_file(
        tmp_path, "classes.csv",
        "name,code\n" + "".join(f"Class {code},{code}\n" for code in class_codes),
    )

    assert import_reference_data(
        connection, accounts_csv=accounts, classes_csv=classes,
    ) == {"accounts": 4, "classes": len(class_codes)}
    for code in ("4.1", "4.10", "004.10"):
        row = connection.execute(
            "SELECT account_id, kind FROM accounts WHERE account_id=?", (code,),
        ).fetchone()
        assert tuple(row) == (code, "expense")
    assert connection.execute(
        "SELECT name FROM accounts WHERE account_id='5170'",
    ).fetchone()[0] == "Medical-General, Not Psychology"
    assert {row[0] for row in connection.execute(
        "SELECT class_code FROM classes",
    )} == set(class_codes)


def test_codes_are_text_of_letters_numbers_periods_and_underscores():
    for code in ["1000", "0010", "0", "1", "A", "1A_2", "7.5", "4.10", "004.10"]:
        assert normalize_code(code) == code
    for bad in [None, "", "_1000", ".7", "10/00", "X" * 33]:
        with pytest.raises(ReferenceDataError):
            normalize_code(bad)


def test_csv_validation_command_fails_with_file_and_line(tmp_path):
    accounts = csv_file(tmp_path, "accounts.csv", "name,code\nSupplies,1000\n")
    classes = csv_file(tmp_path, "classes.csv", "name,code\nProgram,10/00\n")
    result = subprocess.run(
        [sys.executable, "-m", "cash.reference_data", str(accounts), str(classes)],
        capture_output=True, text=True, cwd=MIGRATIONS.parent)
    assert result.returncode == 1
    assert "classes.csv line 2: invalid code" in result.stderr
    assert not (tmp_path / "cash.db").exists()


def test_csv_validation_command_accepts_numeric_and_dotted_codes(tmp_path):
    accounts = csv_file(tmp_path, "accounts.csv", "name,code\nSupplies,0010\n")
    classes = csv_file(tmp_path, "classes.csv", "name,code\nProgram,004.10\n")
    result = subprocess.run(
        [sys.executable, "-m", "cash.reference_data", str(accounts), str(classes)],
        capture_output=True, text=True, cwd=MIGRATIONS.parent)
    assert result.returncode == 0, result.stderr
    assert "Validated 1 account codes and 1 class codes" in result.stdout
    assert not (tmp_path / "cash.db").exists()
