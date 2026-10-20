"""Time one complete authenticated capture in a fresh app/decoder process."""
import argparse
import json
from pathlib import Path
import resource
import tempfile
import time

from cash.qr_worker import cgroup_memory
from cash.document_worker import process_next
from .fixtures import fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='cash27-qr-bench-') as root:
        app, client, headers = fixture(root)
        assert app.config['QR_ENABLED'], 'Set CASH_QR_ENABLED=1'
        start = time.monotonic()
        with args.input.open('rb') as upload:
            response = client.post('/api/documents/capture-merge',
                                   data={'files': (upload, 'image.jpg')}, headers=headers)
        while process_next(Path(root), app.config):
            pass
        result = client.get('/api/documents/inbox').json['data']['documents']
        elapsed = time.monotonic() - start
        assert response.status_code == 200, response.text
        print(json.dumps({'input': args.input.name, 'seconds': round(elapsed, 3),
                          'app_peak_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                          'decoder_peak_rss_mib': round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1),
                          **cgroup_memory(), 'documents': result}))


if __name__ == '__main__':
    main()
