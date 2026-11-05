"""Explicit fictitious admin configuration for disposable test applications."""
import json
import uuid
from pathlib import Path
from cash.web.app import create_app as real_create_app
from cash.files import atomic_write


def create_app(data_root, *, admin_password='test', **options):
    path = Path(options.get('admin_accounts_file') or Path(data_root) / 'config' / 'admins.json').resolve()
    if not path.exists():
        atomic_write(path, json.dumps({'admins': [{'id': str(uuid.uuid4()), 'revision': str(uuid.uuid4()),
                     'name': 'Test Admin', 'password': admin_password, 'enabled': True}]}).encode())
    options.setdefault('user_password', admin_password)
    return real_create_app(data_root, admin_accounts_file=path, **{k: v for k,v in options.items() if k != 'admin_accounts_file'})
