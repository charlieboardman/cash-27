# Server QR experiment

Server branch with deployable container packaging; not deployed to the VM.
No scanner uploader or VM swap changes. The separate scanner daemon now uses
normal auto-sized scans (see DRIVER_DIMENSIONS.md).

## Final application image

Build the root Containerfile and use the production benchmark mode to exercise
the packaged application with both QR and conservative receipt orientation:

```sh
podman build -f Containerfile --target app -t localhost/cash27-capture-final .
.venv/bin/python -m experiments.qr.run_benchmarks /tmp/cash27-final-cases \
  experiments/qr/results/production-capture.jsonl \
  --production --image localhost/cash27-capture-final
```

The harness is mounted read-only, and overrides the image entrypoint solely to
seed an isolated temporary database and start Gunicorn. For receipt ground truth,
an adjacent `name.expected.json` file can contain `{"saved_rotations": [270]}`:
the expected final PDF rotation metadata, established by visually inspecting
the source. This independently checks rotation and unchanged compressed image
streams. `--verify-orientation` instead checks saved voucher QR directions.
Inputs and receipt images stay outside Git. All runs use fresh containers,
one CPU, 512 MiB RAM and no swap unless overridden.

Recreate the new auto-sized receipt cases (the correction is visually established
ground truth for the source PDF, not a model prediction):

```sh
.venv/bin/python -m experiments.qr.receipt_pdf_cases \
  ~/Scans/Inbox/scan-20260914-174818-normal-autosize.pdf /tmp/cash27-final-cases \
  --prefix school --source-correction 0
.venv/bin/python -m experiments.qr.receipt_pdf_cases \
  ~/Scans/Inbox/scan-20260914-175742-871970-page-0001.pdf /tmp/cash27-final-cases \
  --prefix yellow --source-correction 270
.venv/bin/python -m experiments.qr.run_benchmarks /tmp/cash27-final-cases \
  experiments/qr/results/production-receipts.jsonl \
  --production --image localhost/cash27-capture-final \
  --case school-cw-0.pdf --case school-cw-90.pdf --case school-cw-180.pdf --case school-cw-270.pdf \
  --case yellow-cw-0.pdf --case yellow-cw-90.pdf --case yellow-cw-180.pdf --case yellow-cw-270.pdf
```

## Standalone Paddle orientation benchmark

This optional probe uses the official approximately 7 MB ONNX document-orientation
model, independent of the application and QReader image. Downloads happen only
during image construction; inference is offline. Each rotation gets a fresh
container with one CPU and a 256 MiB limit, with swap disabled.

```sh
podman build -f experiments/qr/Containerfile.orientation \
  -t localhost/cash27-orientation-experiment .
.venv/bin/python -m experiments.qr.run_orientation_benchmarks \
  /tmp/cash27-handwritten-osd \
  experiments/qr/results/handwritten-receipt-paddle-orientation.jsonl
.venv/bin/python -m experiments.qr.receipt_orientation_cases /tmp/cash27-orientation-controls
.venv/bin/python -m experiments.qr.run_orientation_benchmarks \
  /tmp/cash27-orientation-controls \
  experiments/qr/results/synthetic-receipt-paddle-orientation.jsonl \
  --prefix receipt --source-correction 0
```

The handwritten input directory contains the existing full-resolution PNG copies
`handwritten-cw-{0,90,180,270}.png`, generated from the private scanned receipt.
The original scan needs a 90° clockwise correction. These images are not in Git.
To recreate the separate, **manually cropped diagnostic** input:

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
from PIL import Image
out = Path('/tmp/cash27-handwritten-cropped')
out.mkdir(exist_ok=True)
with Image.open('/tmp/cash27-handwritten-osd/handwritten-cw-90.png') as image:
    crop = image.crop((2600, 0, 3700, 1750))
    for angle in (0, 90, 180, 270):
        crop.rotate(90-angle, expand=True).save(out / f'handwritten-cw-{angle}.png')
PY
.venv/bin/python -m experiments.qr.run_orientation_benchmarks \
  /tmp/cash27-handwritten-cropped \
  experiments/qr/results/handwritten-receipt-cropped-paddle-orientation.jsonl
```

The benchmark reports individual scores, correctness, import/load/preprocessing/
inference timings, total container wall time, process RSS, and cgroup memory.
These measurements exclude the application and PDF rendering. See RESULTS.md
for accuracy limitations. The final app uses four-view agreement and a 0.90
minimum score; this standalone probe reports each single-view prediction.

## Application experiment

The actual voucher and addendum print routes now emit an inline vector QR in the
bottom-right footer, encoding their exact ID. The date stays at the top right.

Set `CASH_QR_ENABLED=1` to enable recognition for the two admin capture endpoints.
It defaults on in the production image and off in local Python installs.
`CASH_QR_PYTHON` selects a Python environment with the
optional decoder dependencies; `CASH_QR_WEIGHTS` points to predownloaded models;
`CASH_QR_MODEL` selects `n` (default nano) or `s` (small).

Each capture is normalized in staging. A separate Python process loads QReader,
examines all pages, then exits. Rendering is bounded to 2,600 pixels on the long
edge and 200 DPI. The existing 40-page limit applies. A filesystem lock allows
only one decoder across application workers. Busy, timeout (45 seconds), and
failed jobs retain their PDF with a timestamp name and a manual-review message.
There is no persistent decoder or background queue in this experiment.

One unique valid ID gives a name such as `FY27-0002-A1.pdf`. IDs are case-sensitive,
whole-string matches; arbitrary URLs and receipt IDs are ignored. No valid ID,
or multiple distinct valid IDs, gives `scan-YYYYMMDD-HHMMSS-microsecondsZ.pdf`.
The final immutable object name includes the timestamp, internal D ID, and name
hint. Renaming/matching does not rewrite a published PDF.

QR orientation is also read per page, on axis-aligned crops from the original
rendered page. QReader's internally rectified crops are not used for orientation.
ZBar's `UP/LEFT/DOWN/RIGHT` reports map to clockwise corrections of 0/90/180/270
degrees. Corrections compose with existing PDF rotation metadata before immutable
publication; embedded scan images are not re-encoded. A page with multiple
business IDs, conflicting orientations, or no reliable orientation stays as-is.
QR-free receipts currently remain unchanged.

The exact database target must exist and have an active movement and outstanding
paperwork. A second scan or an already filed/void target remains in the inbox.
Matched scans appear in the signout's document list and open the exact review
item with the scan selected. Matching does not post transactions, mark signatures
as reviewed, complete an addendum, or close a signout. Normal human filing still
uses the existing commands. Association is separate metadata in migration 0011.

## Reproduce locally in Podman

From the repository root, with the normal development environment installed:

```sh
.venv/bin/python -m experiments.qr.generate_cases /tmp/cash27-qr-cases
podman build -f experiments/qr/Containerfile -t localhost/cash27-qr-experiment .
python3 -m experiments.qr.run_benchmarks /tmp/cash27-qr-cases /tmp/nano.jsonl
python3 -m experiments.qr.run_benchmarks /tmp/cash27-qr-cases /tmp/small.jsonl --model s
```

Fixture generation uses Playwright Chromium, Pillow, pypdf, and `pdftoppm`.
The image installs CPU-only Torch/Torchvision and caches both models at build
time. The full measured package versions are in `environment.lock.txt`; top-level
model/runtime versions are pinned in the Containerfile and requirements file.

Each benchmark uses a fresh container with one CPU, a **512 MiB combined budget**,
`--memory-swap=512m` (swap disabled), no network, a read-only filesystem, a
128 MiB `/tmp`, dropped capabilities, and a 256-process limit. Gunicorn runs one
worker, as in production. A small HTTP benchmark driver in the same container
logs in, seeds dummy records, uploads a file, verifies the saved PDF and health
endpoint, and checks that no decoder remains. Its overhead counts in the limit.

Use `--memory 384m`, `--model s`, `--memory-swap 1g` (512 MiB RAM plus up to
512 MiB swap), or repeated `--case filename` arguments for
additional runs. The earlier `benchmark.py` uses Flask's test client for isolating
application behavior; the reported decision benchmarks use `benchmark_http.py`.

Measurements start just before the upload request. They include PDF conversion,
model import/load, recognition, naming, and matching; they exclude container boot,
image construction, fixture seeding, and model downloads. Each request reloads
the model. Host filesystem caches may be warm. Container `memory.peak` includes
charged memory/cache; RSS also includes mapped shared pages, so the two metrics
must not be added together. These are local CPU timings, not VM latency claims.

## Limits before any deployment decision

These samples are synthetic transformations of the actual printed template,
not photographs of a physical printout. Real phone photos still need checking.
The measured sample set does not guarantee decoding every supported document.

Recognition currently runs synchronously in the upload request. The existing
single Gunicorn worker cannot serve other requests during that operation.
Uploads exceeding the recognition deadline fall back to manual review. There is
no automatic retry of busy or failed recognition. The image includes both models
for comparison; a production image could retain only the chosen one.

This experiment does not change `compose.yaml`, the production Containerfile,
the scanner daemon, or the VM. Enabling swap is a separate host operation; it
would not let a container swap while its memory+swap limit equals its RAM limit.

## Orientation checks

```sh
.venv/bin/python -m experiments.qr.orientation_cases /path/to/scan.pdf /tmp/orientation-cases
python3 -m experiments.qr.run_benchmarks /tmp/orientation-cases /tmp/orientation.jsonl \
  --verify-orientation --case mixed-four-directions.pdf --case metadata-cw-90.pdf
```

The independent verifier checks the saved PDF's QR directions and exact compressed
image streams. Its verification work occurs after the timed upload and recorded
peak. Fixture images are test copies only; the original scan is not changed.

Generic receipt orientation is a separate **standalone feasibility probe**, not
an enabled application fallback:

```sh
.venv/bin/python -m experiments.qr.receipt_orientation_cases /tmp/osd-cases
podman build -f experiments/qr/Containerfile.osd -t localhost/cash27-osd-experiment .
podman run --rm --network=none --read-only \
  --tmpfs=/tmp:rw,nosuid,nodev,noexec,size=16m \
  --memory=128m --memory-swap=128m --cpus=1 --cap-drop=ALL \
  --security-opt=no-new-privileges -v /tmp/osd-cases:/cases:ro \
  localhost/cash27-osd-experiment
```
