#!/usr/bin/env python3
"""Count the maintained frontend sources: handwritten JavaScript, CSS, Jinja,
and web Python.

Usage: python tools/frontend_counts.py [--revision REF]
Counts the working tree, or the tracked files at REF. Physical lines include
comments and blank lines; nonblank lines, bytes, and file counts give context,
because line counts alone do not show simpler code. Vendor sources, tests,
generated files, and runtime data are never counted.
"""

import argparse
import json
from pathlib import Path
import subprocess

GROUPS = {'javascript': 'cash/static/js/', 'css': 'cash/static/css/',
          'templates': 'cash/templates/', 'web_python': 'cash/web/'}


def measure(revision=None):
    command = ['git', 'ls-tree', '-r', '--name-only', revision] if revision else [
        'git', 'ls-files', '--cached', '--others', '--exclude-standard']
    paths = set(subprocess.check_output(command, text=True).splitlines())
    groups = {name: {'physical': 0, 'nonblank': 0, 'bytes': 0, 'files': 0} for name in GROUPS}
    for path in sorted(paths):
        if not revision and not Path(path).is_file():
            continue
        if Path(path).suffix not in {'.js', '.css', '.html', '.py'}:
            continue
        group = next((name for name, prefix in GROUPS.items() if path.startswith(prefix)), None)
        if not group:
            continue
        source = subprocess.check_output(['git', 'show', f'{revision}:{path}']) if revision else Path(path).read_bytes()
        lines = source.decode().splitlines()
        counts = groups[group]
        counts['physical'] += len(lines)
        counts['nonblank'] += sum(bool(line.strip()) for line in lines)
        counts['bytes'] += len(source)
        counts['files'] += 1
    groups['total'] = {key: sum(group[key] for group in groups.values())
                       for key in ('physical', 'nonblank', 'bytes', 'files')}
    return groups


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision')
    print(json.dumps(measure(parser.parse_args().revision), indent=2))
