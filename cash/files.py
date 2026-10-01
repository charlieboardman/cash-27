"""Small configuration files on disk, replaced atomically."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def atomic_write(path: Path, data: bytes) -> None:
    """Replace path with data so a reader never sees a partial file: a private
    (0600) temporary file beside it is synced, renamed over it, and the
    directory synced."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
