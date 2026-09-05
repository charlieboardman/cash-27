"""Import administrator-maintained account and class code CSV files."""

from __future__ import annotations

import csv
import argparse
import re
import sqlite3
from pathlib import Path


CODE = re.compile(r"[A-Z0-9][A-Z0-9_.]{0,31}")


class ReferenceDataError(ValueError):
    """A reference-data CSV cannot be imported safely."""


def normalize_code(value: object) -> str:
    """Codes are text identifiers, preserving periods and leading/trailing zeros."""
    code = str(value if value is not None else "").strip().upper().replace(" ", "_")
    if not CODE.fullmatch(code):
        raise ReferenceDataError(
            "Use 1-32 letters, numbers, periods, or underscores, starting with a letter or number")
    return code


def _rows(path: str | Path, label: str) -> list[tuple[str, str]]:
    source = Path(path)
    if not source.is_file():
        raise ReferenceDataError(f"{label} CSV not found: {source}")
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["name", "code"]:
            raise ReferenceDataError(
                f"{source.name} must have exactly these headers: name,code")
        parsed = []
        seen = set()
        for line_number, row in enumerate(reader, start=2):
            name = str(row.get("name") or "").strip()
            if not name:
                raise ReferenceDataError(
                    f"{source.name} line {line_number}: name is required")
            try:
                code = normalize_code(row.get("code"))
            except ReferenceDataError as exc:
                raise ReferenceDataError(
                    f"{source.name} line {line_number}: invalid code {row.get('code')!r}; "
                    f"{exc}") from exc
            if code in seen:
                raise ReferenceDataError(
                    f"{source.name} line {line_number}: duplicate code {code}")
            seen.add(code)
            parsed.append((name, code))
    return parsed


def import_reference_data(connection: sqlite3.Connection, *,
                          accounts_csv: str | Path,
                          classes_csv: str | Path) -> dict[str, int]:
    """Add or refresh expense accounts and classes in one transaction.

    Missing CSV rows never deactivate or delete database records, preserving
    historical references and browser-created codes.
    """
    accounts = _rows(accounts_csv, "Accounts")
    classes = _rows(classes_csv, "Classes")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for name, code in accounts:
            existing = connection.execute(
                "SELECT kind FROM accounts WHERE account_id=?", (code,)
            ).fetchone()
            if existing and existing["kind"] != "expense":
                raise ReferenceDataError(
                    f"Account code {code} is reserved for {existing['kind']}")
            if code == "UNALLOCATED_EXPENSE":
                raise ReferenceDataError(
                    "UNALLOCATED_EXPENSE is managed by cash-27")
            connection.execute(
                "INSERT INTO accounts (account_id, name, kind, active)"
                " VALUES (?, ?, 'expense', 1)"
                " ON CONFLICT(account_id) DO UPDATE SET"
                " name=excluded.name, active=1",
                (code, name),
            )
        for name, code in classes:
            connection.execute(
                "INSERT INTO classes (class_code, name, active) VALUES (?, ?, 1)"
                " ON CONFLICT(class_code) DO UPDATE SET"
                " name=excluded.name, active=1",
                (code, name),
            )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    return {"accounts": len(accounts), "classes": len(classes)}


def main() -> None:
    """Check both CSVs before deployment without opening the live database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("accounts_csv", type=Path)
    parser.add_argument("classes_csv", type=Path)
    args = parser.parse_args()
    try:
        accounts = _rows(args.accounts_csv, "Accounts")
        classes = _rows(args.classes_csv, "Classes")
    except (ReferenceDataError, OSError, UnicodeError, csv.Error) as exc:
        parser.exit(1, f"Reference data validation failed: {exc}\n")
    print(f"Validated {len(accounts)} account codes and {len(classes)} class codes")


if __name__ == "__main__":
    main()
