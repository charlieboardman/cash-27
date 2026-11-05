"""Per-submission approver names retained for document reprints.

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


def _write(path, name, paper_names=None):
    try:
        payload = {'approver_name': name}
        if paper_names:
            payload['paper_names'] = {code: validate_approver(value) for code, value in paper_names.items()}
        atomic_write(path, (json.dumps(payload, ensure_ascii=False, indent=2)
                            + '\n').encode('utf-8'))
    except OSError as exc:
        raise ValidationFailure('approver_config', 'Could not save the voucher approver configuration.') from exc


def save_selection(data_root, submission_id, name, paper_names=None):
    path = Path(data_root) / 'config' / 'voucher-approvers' / f'{uuid.UUID(submission_id)}.json'
    _write(path, validate_approver(name), paper_names)


def selected_approver(data_root, submission_id, document_code=None):
    if not submission_id:
        return ''
    path = Path(data_root) / 'config' / 'voucher-approvers' / f'{uuid.UUID(submission_id)}.json'
    if document_code and path.exists():
        try:
            selected = json.loads(path.read_text()).get('paper_names', {})
            if document_code in selected:
                return validate_approver(selected[document_code])
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise ValidationFailure('approver_config', 'Could not read the voucher approver configuration.') from exc
    return _read(path)
