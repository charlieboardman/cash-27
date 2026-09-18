"""Disk-backed print settings and per-submission approver selections.

Selections are written before the existing transaction command commits. Reprints
look up its existing submission ID; no approver data is stored in SQLite. Failed
commands may leave an unused file, which a retry can safely replace.
"""
import json
import os
from pathlib import Path
import tempfile
import uuid

from .commands import ValidationFailure


def validate_approver(value):
    if not isinstance(value, str) or len(value.strip()) > 120 or any(
            ord(c) < 32 for c in value):
        raise ValidationFailure('bad_approver', 'Enter an approver name of at most 120 characters on one line.',
                                {'approver_name': 'single-line name, at most 120 characters'})
    return value.strip()


def _read(path):
    try:
        return validate_approver(json.loads(path.read_text(encoding='utf-8'))['approver_name'])
    except FileNotFoundError:
        return ''
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValidationFailure('approver_config', 'Could not read the voucher approver configuration.') from exc


def _write(path, name):
    """Atomic replacement prevents readers seeing partially written JSON."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            json.dump({'approver_name': name}, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise ValidationFailure('approver_config', 'Could not save the voucher approver configuration.') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def approver_name(data_root, override=None):
    if override is not None:
        return validate_approver(override)
    return _read(Path(data_root) / 'config' / 'voucher-defaults.json')


def save_default(data_root, value):
    name = validate_approver(value)
    _write(Path(data_root) / 'config' / 'voucher-defaults.json', name)
    return name


def save_selection(data_root, submission_id, name):
    path = Path(data_root) / 'config' / 'voucher-approvers' / f'{uuid.UUID(submission_id)}.json'
    _write(path, validate_approver(name))


def selected_approver(data_root, submission_id):
    if not submission_id:
        return ''
    path = Path(data_root) / 'config' / 'voucher-approvers' / f'{uuid.UUID(submission_id)}.json'
    return _read(path)
