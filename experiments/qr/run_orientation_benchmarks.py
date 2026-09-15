"""Run four rotations in separate CPU-only, memory-limited Podman containers."""
import argparse
import json
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument('cases', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--prefix', default='handwritten')
parser.add_argument('--source-correction', type=int, default=90)
parser.add_argument('--memory', default='256m')
args = parser.parse_args()
with args.output.open('w') as output:
    for angle in (0, 90, 180, 270):
        case = f'{args.prefix}-cw-{angle}.png'
        expected = (args.source_correction - angle) % 360
        command = [
            'podman', 'run', '--rm', '--network=none', '--read-only',
            '--cpus=1', f'--memory={args.memory}', f'--memory-swap={args.memory}',
            '--pids-limit=64', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '-v', f'{args.cases.resolve()}:/cases:ro',
            'localhost/cash27-orientation-experiment',
            f'/cases/{case}', '--expected-cw', str(expected),
        ]
        start = time.perf_counter()
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        elapsed = time.perf_counter() - start
        if result.returncode:
            raise RuntimeError(f'{case}: {result.returncode}\n{result.stderr}')
        record = json.loads(result.stdout)
        record.update(container_wall_seconds=elapsed, container_memory_limit=args.memory,
                      container_swap_enabled=False, container_cpus=1)
        output.write(json.dumps(record) + '\n')
        output.flush()
        print(json.dumps(record), flush=True)
