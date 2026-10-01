"""Disk-backed print settings and per-submission approver selections.

Selections are written before the existing transaction command commits. Reprints
look up its existing submission ID; no approver data is stored in SQLite. Failed
commands may leave an unused file, which a retry can safely replace.
"""
import json
from pathlib import Path
import uuid

from .commands import ValidationFailure
from .files import atomic_write
from .paper import NAME


def validate_approver(value):
    # The name prints on vouchers and addenda, which must stay one page (paper.py).
    if not isinstance(value, str) or len(value.strip()) > NAME or any(
            ord(c) < 32 for c in value):
        raise ValidationFailure('bad_approver', f'Enter an approver name of at most {NAME} characters on one line.',
                                {'approver_name': f'single-line name, at most {NAME} characters'})
    return value.strip()


def _read(path):
    try:
        return validate_approver(json.loads(path.read_text(encoding='utf-8'))['approver_name'])
    except FileNotFoundError:
        return ''
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValidationFailure('approver_config', 'Could not read the voucher approver configuration.') from exc


def _write(path, name):
    try:
        atomic_write(path, (json.dumps({'approver_name': name}, ensure_ascii=False, indent=2)
                            + '\n').encode('utf-8'))
    except OSError as exc:
        raise ValidationFailure('approver_config', 'Could not save the voucher approver configuration.') from exc


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
