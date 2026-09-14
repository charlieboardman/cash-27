"""Measure the real Gunicorn upload path inside a constrained Podman container."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from cash.qr_worker import cgroup_memory


def verify_orientation(original_path, saved):
    """Independently inspect the saved pages and their compressed image bytes."""
    import io
    import numpy as np
    import pypdfium2 as pdfium
    from pypdf import PdfReader
    from pyzbar.pyzbar import decode, ZBarSymbol
    from cash.qr_worker import valid_ids

    before = PdfReader(original_path)
    after = PdfReader(io.BytesIO(saved))
    assert len(before.pages) == len(after.pages)
    def streams(page):
        return [value.get_object()._data for value in
                page['/Resources']['/XObject'].values()]
    for old, new in zip(before.pages, after.pages):
        assert streams(old) == streams(new), 'Image data changed'
    pdf = pdfium.PdfDocument(saved)
    orientations = []
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            bitmap = page.render(scale=2600 / max(page.get_size()))
            try:
                image = np.asarray(bitmap.to_pil().convert('RGB'))
                results = decode(image, symbols=[ZBarSymbol.QRCODE])
                directions = [r.orientation for r in results
                              if valid_ids([r.data.decode('utf-8', errors='replace')])]
                assert directions and set(directions) == {'UP'}, (index, directions)
                orientations.append(directions)
                del image
            finally:
                bitmap.close()
                page.close()
    finally:
        pdf.close()
    return {'saved_orientations': orientations, 'image_streams_unchanged': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    assert os.environ.get('CASH_QR_ENABLED') == '1'
    with tempfile.TemporaryDirectory(prefix='cash27-http-') as root:
        env = {**os.environ, 'CASH_DATA_ROOT': root, 'CASH_ADMIN_PASSWORD': 'benchmark-only',
               'CASH_USER_PASSWORD': 'benchmark-only', 'CASH_SECRET_KEY': 'temporary-benchmark-key'}
        server = subprocess.Popen(['gunicorn', '--workers=1', '--bind=127.0.0.1:8000',
                                   '--timeout=120', 'run_prod:app'], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        headers = {}
        def request(path, data=None, content_type='application/json'):
            if data is not None and content_type == 'application/json':
                data = json.dumps(data).encode()
            req = urllib.request.Request('http://127.0.0.1:8000' + path, data=data,
                                         headers={**headers, 'Content-Type': content_type})
            with urllib.request.urlopen(req, timeout=60) as response:
                cookie = response.headers.get('Set-Cookie')
                if cookie:
                    # Local-only benchmark deliberately sends the Secure cookie over loopback.
                    headers['Cookie'] = cookie.split(';')[0]
                payload = json.load(response)
                assert payload['ok'], payload
                return payload['data']
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    request('/api/health')
                    break
                except urllib.error.URLError:
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise
                    time.sleep(.05)
            headers['X-CSRF'] = request('/api/login', {'password': 'benchmark-only'})['csrf']
            def post(path, **data):
                return request(path, {'submission_id': str(uuid.uuid4()), **data})
            post('/api/entries/opening', date='2026-09-01', usd='2000', mxn='15000', assigned_rate_milli=15000)
            for recipient in ['Other Example', 'Alex Example']:
                post('/api/vouchers/prepare', type='signout', date='2026-09-14',
                     recipient=recipient, purpose='Supplies', usd='100', mxn='1500')
            post('/api/addenda/prepare', voucher_id='FY27-0002', kind='positive', date='2026-09-14',
                 reason='More supplies', usd='20', mxn='0')
            boundary = 'cash27-' + uuid.uuid4().hex
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="image.jpg"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n').encode()
            body += args.input.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            start = time.monotonic()
            result = request('/api/documents/capture-merge', body, 'multipart/form-data; boundary=' + boundary)
            elapsed = time.monotonic() - start
            # The normal document must remain readable, including on decode failure/OOM.
            with urllib.request.urlopen(urllib.request.Request(
                    'http://127.0.0.1:8000/api/documents/' + result['document_id'] + '/stream',
                    headers=headers)) as response:
                saved = response.read()
                assert saved.startswith(b'%PDF')
            health = request('/api/health')
            running_decoders = [p.name for p in Path('/proc').iterdir() if p.name.isdigit()
                                and b'cash.qr_worker' in (p / 'cmdline').read_bytes()]
            assert not running_decoders, running_decoders
            metrics = cgroup_memory()
            verification = (verify_orientation(args.input, saved)
                            if os.environ.get('CASH_BENCH_VERIFY_ORIENTATION') == '1' else {})
        finally:
            server.terminate()
            server.wait(timeout=15)
        print(json.dumps({'input': args.input.name, 'harness': 'gunicorn-http',
                          'seconds': round(elapsed, 3), 'health_after': health['status'],
                          'decoders_after': 0, 'process_peak_rss_mib': round(resource.getrusage(
                              resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1),
                          **metrics, **verification, **result}))


if __name__ == '__main__':
    main()
