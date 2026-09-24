"""Initialize and verify a persistent data root before container startup."""

from __future__ import annotations

import os
from pathlib import Path

from .db import apply_migrations, connect
from .reference_data import import_reference_data
from .web.app import DATA_LAYOUT


def main() -> None:
    data_root = Path(os.environ.get("CASH_DATA_ROOT", "/data"))
    for name in DATA_LAYOUT:
        (data_root / name).mkdir(parents=True, exist_ok=True, mode=0o750)

    connection = connect(data_root / "cash.db")
    try:
        apply_migrations(connection,
                         Path(__file__).resolve().parent.parent / "migrations",
                         allow_newer=True)
        application_root = Path(__file__).resolve().parent.parent
        imported = import_reference_data(
            connection,
            accounts_csv=os.environ.get(
                "CASH_ACCOUNTS_CSV", application_root / "accounts.csv"),
            classes_csv=os.environ.get(
                "CASH_CLASSES_CSV", application_root / "classes.csv"),
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SystemExit(f"database integrity check failed: {integrity}")
    finally:
        connection.close()
    print(
        f"Verified cash-27 data at {data_root}; imported "
        f"{imported['accounts']} account codes and "
        f"{imported['classes']} class codes",
        flush=True,
    )


if __name__ == "__main__":
    main()
