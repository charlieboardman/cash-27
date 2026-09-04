"""Client for the private backup-core service."""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def create_archive(destination: str, name: str) -> dict:
    url = os.environ.get(
        "CASH_BACKUP_CORE_URL", "http://backup-core:8090/archive")
    token = os.environ.get("CASH_BACKUP_CORE_TOKEN", "")
    attempts = int(os.environ.get("CASH_BACKUP_CORE_ATTEMPTS", "60"))
    delay = float(os.environ.get("CASH_BACKUP_CORE_DELAY", "2"))
    timeout = float(os.environ.get("CASH_BACKUP_CORE_TIMEOUT", "7200"))
    payload = json.dumps({
        "destination": destination, "name": name,
    }).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = Request(
            url, data=payload, method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            })
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.load(response)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "backup core failed"))
            return result
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(
                f"backup core returned HTTP {exc.code}: {detail}")
            if exc.code < 500:
                break
        except (OSError, URLError, ValueError, RuntimeError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise SystemExit(f"backup core is unavailable: {last_error}")


if __name__ == "__main__":
    command, *arguments = sys.argv[1:]
    if command == "create" and len(arguments) == 2:
        print(json.dumps(create_archive(arguments[0], arguments[1])))
    else:
        raise SystemExit(
            "usage: python -m cash.backup_client create outbox|restore NAME")
