"""Named administrators in a private, atomically replaced configuration file."""
import fcntl
import json
import os
import secrets
import uuid
from pathlib import Path

from .commands import ValidationFailure
from .files import atomic_write
from .user_access import validate_password
from .voucher_defaults import validate_approver


def read(path):
    try:
        accounts = json.loads(Path(path).read_text())['admins']
        if not isinstance(accounts, list) or not accounts:
            raise ValueError('invalid accounts')
        ids, passwords = set(), set()
        for account in accounts:
            if not isinstance(account, dict):
                raise ValueError('invalid account')
            if str(uuid.UUID(account['id'])) != account['id']:
                raise ValueError('invalid account ID')
            if str(uuid.UUID(account['revision'])) != account['revision']:
                raise ValueError('invalid revision')
            if not account['name'] or validate_approver(account['name']) != account['name']:
                raise ValueError('invalid name')
            validate_password(account['password'])
            if type(account['enabled']) is not bool:
                raise ValueError('invalid enabled flag')
            if account['id'] in ids or account['password'] in passwords:
                raise ValueError('duplicate account')
            ids.add(account['id'])
            passwords.add(account['password'])
        if not any(a['enabled'] for a in accounts):
            raise ValueError('no active administrator')
        return accounts
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ValidationFailure) as exc:
        raise ValidationFailure('admin_config', 'Administrator configuration unavailable') from exc


def public(account):
    return {key: account[key] for key in ('id', 'name', 'enabled')}


def matches(account, password):
    return isinstance(password, str) and secrets.compare_digest(
        account['password'].encode(), password.encode())


def authenticate(path, password):
    return next((a for a in read(path) if a['enabled'] and matches(a, password)), None)


def change(path, actor_id, actor_revision, *, account_id=None, name=None,
           password=None, enabled=None, delete=False):
    path = Path(path)
    # A separate lock survives atomic replacement, including across workers.
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        accounts = read(path)
        if not any(a['id'] == actor_id and a['revision'] == actor_revision and a['enabled'] for a in accounts):
            raise ValidationFailure('unauthorized', 'Login required')
        target = next((a for a in accounts if a['id'] == account_id), None)
        if account_id and target is None:
            raise ValidationFailure('bad_admin', 'Administrator not found')
        if delete:
            accounts.remove(target)
        else:
            if target is None:
                target = {'id': str(uuid.uuid4()), 'revision': str(uuid.uuid4()), 'enabled': True}
                accounts.append(target)
            if name is not None:
                target['name'] = validate_approver(name)
            if not target.get('name'):
                raise ValidationFailure('bad_admin', 'Name required')
            if password is not None:
                password = validate_password(password)
                if any(a is not target and a.get('password') == password for a in accounts):
                    raise ValidationFailure('bad_admin', 'Password already assigned')
                if target.get('password') != password:
                    target['revision'] = str(uuid.uuid4())
                target['password'] = password
            if not target.get('password'):
                raise ValidationFailure('bad_admin', 'Password required')
            if enabled is not None:
                if type(enabled) is not bool:
                    raise ValidationFailure('bad_admin', 'Invalid account status')
                if enabled != target['enabled']:
                    target['revision'] = str(uuid.uuid4())
                target['enabled'] = enabled
        if not any(a['enabled'] for a in accounts):
            raise ValidationFailure('last_admin', 'At least one active administrator required')
        try:
            atomic_write(path, (json.dumps({'admins': accounts}, ensure_ascii=False, indent=2) + '\n').encode())
        except OSError as exc:
            raise ValidationFailure('admin_config', 'Administrator configuration unavailable') from exc
        return [public(a) for a in accounts]
