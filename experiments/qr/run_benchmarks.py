"""Run fresh Podman containers with a shared app+decoder resource budget."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('cases', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--image', default='localhost/cash27-qr-experiment')
    parser.add_argument('--memory', default='512m')
    parser.add_argument('--memory-swap', help='Combined RAM+swap limit; default disables swap')
    parser.add_argument('--verify-orientation', action='store_true')
    parser.add_argument('--production', action='store_true', help='Mount harness into the production app image')
    parser.add_argument('--case', action='append', dest='selected')
    args = parser.parse_args()
    cases = args.selected or ['voucher.pdf', 'addendum.pdf', 'scan.jpg', 'rotated.jpg',
                              'perspective-dim.jpg', 'phone-12mp.jpg', 'no-qr-12mp.jpg',
                              'same-id-3-pages.pdf', 'conflicting-ids.pdf']
    with args.output.open('w') as out:
        for case in cases:
            command = ['podman', 'run', '--rm', '--network=none', '--read-only',
                       '--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=128m',
                       '--cap-drop=ALL', '--security-opt=no-new-privileges',
                       '--pids-limit=256', '--cpus=1', f'--memory={args.memory}',
                       f'--memory-swap={args.memory_swap or args.memory}',
                       '-e', f'CASH_BENCH_VERIFY_ORIENTATION={int(args.verify_orientation)}',
                       '-v', f'{args.cases.resolve()}:/cases:ro']
            if args.production:
                command += ['-v', f'{Path(__file__).resolve().parent}:/app/experiments/qr:ro',
                            '--entrypoint=python']
            command += [args.image]
            if args.production:
                command += ['-m', 'experiments.qr.benchmark_http']
            command += [f'/cases/{case}']
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            record = {'decoder': 'zxing-cpp-3.1.1', 'memory_limit': args.memory,
                      'swap': args.memory_swap or 'disabled',
                      'exit_code': result.returncode, 'input': case}
            try:
                record.update(json.loads(result.stdout.strip().splitlines()[-1]))
            except (ValueError, IndexError):
                record['error'] = (result.stderr or result.stdout)[-2000:]
            out.write(json.dumps(record) + '\n')
            out.flush()
            print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
