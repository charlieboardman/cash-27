"""Read and edit CASH_USER_PASSWORD in the installer's existing environment file.

Use the same literal KEY=value format as setup.sh; never source/evaluate it.
The enclosing directory is mounted so atomic replacement is visible on the host.
"""
from pathlib import Path

from .commands import ValidationFailure
from .files import atomic_write

KEY = 'CASH_USER_PASSWORD='


def validate_password(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 1024 or value.splitlines() != [value] or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValidationFailure('bad_user_password',
                                'Enter a password of at most 1024 characters, without line breaks or surrounding whitespace.',
                                {'password': 'nonempty, single line, no surrounding whitespace'})
    return value


def _read(path):
    content = Path(path).read_bytes().decode('utf-8')
    lines = content.splitlines(keepends=True)
    positions = [i for i, line in enumerate(lines) if line.startswith(KEY)]
    if len(positions) != 1:
        raise ValueError('expected one user password setting')
    return lines, positions[0]


def password(env_file, fallback):
    if not env_file:
        return fallback  # Development/test apps without an installed environment file.
    try:
        lines, index = _read(env_file)
        return validate_password(lines[index][len(KEY):].rstrip('\r\n'))
    except (OSError, ValueError, ValidationFailure) as exc:
        raise ValidationFailure('user_password_config',
                                'The user password configuration could not be read. Contact the administrator.') from exc


def save_password(env_file, value):
    value = validate_password(value)
    if not env_file:
        raise ValidationFailure('user_password_config', 'The installed environment file is not configured for this app.')
    path = Path(env_file)
    try:
        lines, index = _read(path)
        ending = '\r\n' if lines[index].endswith('\r\n') else '\n' if lines[index].endswith('\n') else ''
        lines[index] = KEY + value + ending
        atomic_write(path, ''.join(lines).encode('utf-8'))
    except (OSError, ValueError) as exc:
        raise ValidationFailure('user_password_config',
                                'The user password could not be saved to the installed environment file.') from exc
