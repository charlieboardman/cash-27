"""Local two-container responsiveness check using the production image and limits.

Run: .venv/bin/python -m experiments.qr.benchmark_async --image localhost/cash27-async-check
Uses dummy vouchers only, random loopback ports, and a disposable data directory.
"""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
import uuid

from pypdf import PdfReader, PdfWriter
from .fixtures import fixture


def podman(*args):
    return subprocess.check_output(['podman', *map(str, args)], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    names = ['cash27-async-web-' + uuid.uuid4().hex[:8], 'cash27-async-worker-' + uuid.uuid4().hex[:8]]
    with tempfile.TemporaryDirectory(prefix='cash27-async-benchmark-') as temp:
        root = Path(temp)
        data = root / 'data'
        fixture(data)
        subprocess.run(['.venv/bin/python', '-m', 'experiments.qr.generate_cases', str(root / 'cases')],
                       check=True, stdout=subprocess.DEVNULL)
        original = root / 'cases' / 'voucher.pdf'
        writer = PdfWriter()
        writer.add_page(PdfReader(original).pages[0].rotate(180))
        backwards = root / 'cases' / 'backwards.pdf'
        with backwards.open('wb') as output:
            writer.write(output)
        common = ['--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                  '--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=128m', '--pids-limit=128',
                  '-v', f'{data}:/data:rw,z', '-e', 'CASH_DATA_ROOT=/data', '-e', 'HOME=/tmp',
                  '-e', 'CASH_ADMIN_PASSWORD=benchmark-only', '-e', 'CASH_USER_PASSWORD=benchmark-only',
                  '-e', 'CASH_SECRET_KEY=temporary-local-benchmark']
        try:
            podman('run', '-d', '--name', names[0], *common, '--memory=512m',
                   '-p', '127.0.0.1::8000', args.image)
            address = podman('port', names[0], '8000/tcp')
            headers = {}
            def request(path, body=None, content_type='application/json'):
                if body is not None and not isinstance(body, bytes):
                    body = json.dumps(body).encode()
                req = urllib.request.Request('http://' + address + path, data=body,
                                             headers={**headers, 'Content-Type': content_type})
                with urllib.request.urlopen(req, timeout=10) as response:
                    cookie = response.headers.get('Set-Cookie')
                    if cookie:
                        headers['Cookie'] = cookie.split(';')[0]
                    contents = response.read()
                    return json.loads(contents)['data'] if content_type == 'application/json' else contents
            for _ in range(60):
                try:
                    request('/api/health')
                    break
                except Exception:
                    time.sleep(.5)
            headers['X-CSRF'] = request('/api/login', {'password': 'benchmark-only'})['csrf']
            podman('run', '-d', '--name', names[1], *common, '--memory=512m',
                   '--cpus=0.5', '--network=none', '--entrypoint=python', args.image,
                   '-m', 'cash.document_worker', 'loop')
            def limits():
                return json.loads(podman('exec', names[1], 'python', '-c',
                    "from pathlib import Path; import json; print(json.dumps({name: Path('/sys/fs/cgroup', name).read_text().strip() for name in ['cpu.max','cpu.stat','memory.max','memory.swap.max','memory.current','memory.peak','memory.events']}))"))
            initial = limits()
            assert initial['cpu.max'] == '50000 100000', initial
            assert initial['memory.max'] == str(512 * 1024 ** 2), initial
            assert initial['memory.swap.max'] == str(512 * 1024 ** 2), initial
            uploads = []
            started = time.monotonic()
            for path in [original, backwards, root / 'cases' / 'phone-12mp.jpg']:
                boundary = 'bench-' + uuid.uuid4().hex
                payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{path.name}"\r\n'
                           'Content-Type: application/octet-stream\r\n\r\n').encode() + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
                t = time.monotonic()
                response = request('/api/documents/capture', payload, 'multipart/form-data; boundary=' + boundary)
                result = json.loads(response)['data']['documents'][0]
                assert result['processing']['status'] == 'queued', result
                uploads.append({'file': path.name, 'seconds': round(time.monotonic() - t, 3), 'id': result['document_id']})
            latencies = []
            while time.monotonic() - started < 240:
                for route in ['/api/ledger', '/api/health']:
                    t = time.monotonic()
                    request(route)
                    latencies.append(time.monotonic() - t)
                docs = request('/api/documents/inbox')['documents']
                if all(doc['processing']['ready'] for doc in docs):
                    break
                time.sleep(.25)
            assert len(docs) == 3 and all(doc['processing']['status'] == 'complete' for doc in docs), docs
            assert all(doc['display_name'] == 'FY27-0002.pdf' for doc in docs), docs
            assert sorted(doc['qr']['status'] for doc in docs) == ['duplicate', 'duplicate', 'matched']
            # The reversed paper ends upright, independently of decoding its ID.
            pdf = request('/api/documents/' + uploads[1]['id'] + '/document.pdf', content_type='application/pdf')
            assert PdfReader(io.BytesIO(pdf)).pages[0].rotation % 360 == 0
            report = {'image': args.image, 'uploads': uploads, 'total_seconds': round(time.monotonic() - started, 3),
                      'requests': len(latencies), 'max_request_seconds': round(max(latencies), 3),
                      'p95_request_seconds': round(sorted(latencies)[int(len(latencies) * .95)], 3),
                      'initial_worker': initial, 'final_worker': limits(),
                      'matches': [doc['qr']['status'] for doc in docs], 'backwards_corrected': True}
            print(json.dumps(report, indent=2))
            if args.output:
                args.output.write_text(json.dumps(report, indent=2) + '\n')
        finally:
            for name in reversed(names):
                subprocess.run(['podman', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
