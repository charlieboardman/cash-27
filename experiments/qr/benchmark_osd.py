"""Standalone Tesseract OSD cost/accuracy probe; never rotates uploaded files."""
import json
from pathlib import Path
import resource
import subprocess
import time

for path in sorted(Path('/cases').glob('*.png')):
    start = time.monotonic()
    result = subprocess.run(['tesseract', str(path), 'stdout', '--psm', '0'],
                            capture_output=True, text=True, timeout=20)
    fields = dict(line.split(': ', 1) for line in result.stdout.splitlines() if ': ' in line)
    print(json.dumps({'input': path.name, 'seconds': round(time.monotonic()-start, 3),
                      'exit_code': result.returncode, 'fields': fields,
                      'peak_process_rss_mib': round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/1024, 1),
                      'diagnostic': result.stderr.strip()}), flush=True)
