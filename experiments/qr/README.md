# QR capture benchmarks

The current decoder is `zxing-cpp==3.1.1` (Apache-2.0), with no QR model,
Torch, Ultralytics, QReader, QRDet, or ZBar dependency. Its upstream license is
https://github.com/zxing-cpp/zxing-cpp/blob/v3.1.1/LICENSE; installed wheels retain
third-party license files. The production image separately retains the Paddle
receipt-orientation classifier.

Rendering remains capped at 200 dpi and 2600 pixels on the longest side.
Browser photos remain capped at 2400 pixels with JPEG quality 0.88. ZXing searches
whole rendered pages, including QR codes embedded in larger photographs.
Only exact application document codes count. All codes are read; conflicting IDs
prevent rotation. Native image-coordinate orientations are rounded to the nearest
cardinal direction only within 30 degrees, and every copy must agree. Uncertain
pages remain available for manual review. PDF rotation does not reencode images.

Generate dummy fixtures and run constrained capture tests from the repository root:

```sh
.venv/bin/python -m experiments.qr.generate_cases /tmp/cash27-qr-cases
podman build -f experiments/qr/Containerfile -t cash27-qr-experiment .
.venv/bin/python -m experiments.qr.run_benchmarks /tmp/cash27-qr-cases /tmp/zxing.jsonl
```

The HTTP harness includes upload, durable job processing, and retrieval. A fresh
container has 512 MiB RAM, no swap, one CPU, no network, and a read-only root.
`--verify-orientation` checks saved voucher PDFs and unchanged image streams.
`--production --image IMAGE` runs the same harness against a production build.

For separate web/worker containers and the half-core worker limit:

```sh
podman build -t cash27-zxing .
.venv/bin/python -m experiments.qr.benchmark_async --image localhost/cash27-zxing
```

These commands use disposable databases with dummy records. Private production
photos must remain outside the repository. `phone-12mp.jpg` is an enlarged digital
fixture, not evidence of real camera-photo performance.

## Historical experiments

`RESULTS.md`, JSON reports, and `environment.lock.txt` retain the earlier QReader
and receipt-classifier experiments for comparison. The lock file is a historical
inventory, not an installation manifest. Their timings do not describe ZXing.
The standalone `benchmark_orientation.py` and `benchmark_ollama.py` scripts are
receipt-orientation research tools, not part of the QR runtime.
