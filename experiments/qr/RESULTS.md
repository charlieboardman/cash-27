# ZXing replacement validation — October 2026

The runtime now uses ZXing-C++ 3.1.1. The QReader measurements below are
historical and do not describe the current dependency set.

The production Containerfile built successfully without QReader, QRDet,
Ultralytics, Torch, Torchvision, or pyzbar installed. ZXing's wheel retains its
Apache-2.0 license. The separate Paddle receipt classifier remains installed.

A read-only copy of 13 production source PDFs was tested outside the repository:
three camera photos with QR codes (skew, shadows, clutter, and curled paper), five
QR-bearing scan controls, and five older photos without printed QR codes. Eight
QR inputs at all four cardinal rotations plus five negatives gave **37/37**
correct IDs, per-page IDs, QR-presence flags, and orientation corrections through
`cash.qr_worker.decode_pdf`. Rendering retained the existing 200 dpi / 2600 px cap.
No private PDFs, document IDs, or payloads are included here.

The built production image ran these cases with **512 MiB RAM, half a CPU core,
no swap, no network, and a read-only root**. All 37 completed in 7.93 seconds;
the slowest case took 0.298 seconds. Cgroup peak was 161.0 MiB, reported process
peak RSS was 178.5 MiB, and there were no OOM kills or swap use. These are local
container measurements on the development host, not production VM timings or a
claim that every camera image will decode.

The two-container asynchronous check also passed: uploads were accepted in
0.005–0.070 seconds, all three documents completed in 2.19 seconds, duplicate
matching remained correct, and the upside-down PDF ended upright. The half-core
worker peaked at 148.7 MiB (cgroup), with no OOM kills. Ledger/health requests
stayed below 0.005 seconds in this small local run.

The nine synthetic HTTP fixtures completed in fresh 512 MiB containers with
swap disabled, including the dim perspective image, no-code fallback, multipage
input, and splitting different document IDs. Saved three-page QR orientations
were also checked. The full automated suite passed (620 passed, one opt-in
container-cache test skipped); two subsequently added render-budget cases also
passed, verifying the 200 dpi and 2600 px limits.

## Historical QReader decision report

**Server-side decoding looks viable on the existing VM.** The nano model passed
all nine initial cases under Podman at 512 MiB RAM with swap disabled. The larger
small model needed a little more room for a 12-megapixel image; allowing swap
made that upload succeed. These are feasibility results, not a claim that every
phone photo will decode.

Developed on `feature/server-qr-capture`, September 14, 2026. Production was not
changed and no swap was configured on the VM. The scanner sidebar was completed
separately in brother-ds640-daemon 1.2.0. Sections below retain the experiment's
chronology; the final branch packages both server models and enables the
conservative receipt fallback described in the final measurements section.

## What the branch does

- Prints the exact voucher/addendum ID as a bottom-right QR using the real
  templates. Keeps the date on the right. The existing v3 preview was updated.
- Decodes admin camera/file captures in a short-lived CPU QReader process.
- Names a recognized scan `FY27-0002.pdf` or `FY27-0002-A1.pdf`; uses a UTC
  timestamp if there is no valid ID or the document has conflicting IDs.
- Associates a unique eligible record and opens its review with the scan selected.
  Matched scans appear in the signout's document list. **No signatures, cash
  activity, or completion state are approved automatically.**
- Preserves the PDF for manual review on decoding failure, timeout, or a busy
  decoder. Existing/duplicate/void/unknown targets are not automatically filed.
- Includes the tested CPU dependencies and verified checkpoints in the normal
  deployment image, with recognition enabled. Local Python installs default off.
- Uses the 7 MB receipt classifier only on pages without a valid voucher QR.
  All four rotated views must agree on the correction and score at least 0.90;
  otherwise the page stays unchanged. QR orientation takes precedence. Models
  run sequentially and exit between requests. The threshold is an initial
  conservative rule, not a validated guarantee for arbitrary receipts.

## Final measurements: auto-sized receipts and production packaging

The two new driver-sized PDFs were tested first as extracted full-resolution
rasters, then through the actual production image's HTTP upload path. No manual
crop or image enhancement was used. These are different receipts from the earlier
handwriting and auto-parts samples, so this is not a controlled comparison of
auto-sizing versus cropping.

| Source | Embedded raster | Visually established source correction | Standalone model |
|---|---|---|---|
| `scan-20260914-174818-normal-autosize.pdf` — school | 1619 × 1256 | 0° | 4/4 correct; scores 0.923–0.925 |
| `scan-20260914-175742-871970-page-0001.pdf` — yellow receipt | 1617 × 1254 | 270° clockwise | 4/4 correct; scores 0.902–0.930 |

School PDF SHA-256:
`60be008bc9af5a62e0e4ef9fccb7a0b2284d798dabd192682de9ad4e8e5f98bd`.
Yellow PDF SHA-256:
`a6afa27ccbaea39f1ee3f2dc19b61f5e51d7135f300d1299dc4461da15412651`.
Each raster direction used a fresh offline one-CPU, 256 MiB container, no swap.
Standalone inference was 5.7–10.9 ms, with imports/loading/container startup
dominating wall time. The first cold run took 4.66 seconds and charged 135.1 MiB;
subsequent runs took 1.45–1.82 seconds. These exclude PDF extraction and the app.
Raw results: [school](results/autosized-school-paddle-orientation.jsonl),
[yellow](results/autosized-yellow-paddle-orientation.jsonl).

The integrated tests then uploaded each actual PDF in all four orientations
using page rotation metadata. The server rendered it, ran QReader, ran four
receipt views when no voucher QR was present, and published the rotated PDF.
All **8/8 ended upright** under the agreed four-view/0.90 rule. Ground truth was
the source's visually inspected orientation; verification checked the saved
rotation metadata and original compressed image streams, independently of the
classifier. The source PDFs were not changed.

| Production HTTP input | Cases | Upload time | Peak charged container memory | Outcome |
|---|---:|---:|---:|---|
| Auto-sized school receipt | 4 | 6.48–7.29 s | 454.6 MiB | All upright, images unchanged |
| Auto-sized yellow receipt | 4 | 6.44–6.74 s | 413.3 MiB | All upright, images unchanged |
| Earlier faint handwritten receipt | 1 | 6.84 s | 432.4 MiB | Abstained; original orientation and images retained |

Raw integrated results: [production receipts](results/production-receipts.jsonl).
Each run used a fresh container, one CPU, 512 MiB RAM, no swap, no network, a
read-only image and 128 MiB `/tmp`. Measurements include Gunicorn, normalization,
both sequential subprocesses and the benchmark driver, but exclude container
startup and independent post-upload verification. No OOM kills occurred. Every
upload remained an unmatched timestamp-named inbox document, the app stayed
healthy, and no model subprocess remained. Importing the idle app was separately
verified not to import Torch, QReader, or ONNX Runtime.

The production image also passed voucher/addendum naming and matching, degraded
perspective and 12-megapixel photos, no-code and conflicting-ID fallbacks, and
the 40-page limit case. Single-document uploads in this set took 7.47–10.33 s;
the 40-page voucher PDF took 29.67 s and peaked at 455.6 MiB. Each case had the
expected matching status and filename, a healthy app, and no OOM or leftover
model process. Raw results: [production capture](results/production-capture.jsonl).

All six production QR orientation cases also passed: four raster rotations,
existing PDF rotation metadata, and a four-page document with mixed directions.
Independent ZBar inspection found every saved QR upright and the compressed
images unchanged. The mixed four-page scan took 8.03 s and **reached the
512 MiB ceiling**, without an OOM kill. Thus this limit has little headroom for
some scans; success here is not a guarantee for every multi-page upload.
Raw results: [production QR orientation](results/production-qr-orientation.jsonl).
An intentional 384 MiB rerun killed the decoder, while the app stayed healthy
and returned the readable four-page PDF with a timestamp name for manual review
in 5.26 seconds. This verifies the failure path in the final production image:
[tight-memory result](results/production-tight-memory.jsonl).

The root `Containerfile` now packages both models, with checkpoint SHA-256
verification at build time. The tested image was
`1879e4509e963ae281fc64e03ac5458e66387121a4b5fa74d557076c99355e47`,
2,027,647,786 bytes (1.89 GiB uncompressed). The dependency installation is much
larger than the 7 MB orientation weights and makes initial builds slower.
Compose uses the tested 512 MiB/no-swap app budget; this excludes other VM
services. These local results support a deployment trial on the 1 GB VM but do
not measure its CPU speed, cold disk cache, build memory, or other-service load.
No deployment, scanner upload, or VM swap changes have been performed.

Receipt rotation is deliberately allowed to abstain. Agreement between four
views is correlated evidence, and 0.90 is not a calibrated probability of being
right. Two successful new receipts do not establish general handwriting accuracy.
QR orientation remains the preferred signal and is never overridden. Turning
off `CASH_RECEIPT_ORIENTATION_ENABLED` retains the QR workflow.

Validation: 320 backend tests and 13 selected browser workflow/print tests
passed. Tests cover QR precedence, weak/conflicting receipt predictions,
subprocess failure/timeout/busy handling, the disabled mode, request-wide
recognition deadlines, unchanged image data and final stored hashes, and the
separation between matching and approval. Compose configuration also validated.

## Podman measurements

Real HTTP uploads through Gunicorn (one master, one worker), plus the decoder and
benchmark driver, shared the limit. One CPU; no network; read-only image;
128 MiB writable `/tmp`; all capabilities dropped. Every request started a new
decoder and reloaded its model. Every result below returned a readable PDF,
reported healthy afterward, and left zero decoder processes running.

| Model / input | RAM limit | Swap allowance | Upload time | Peak container RAM | Result |
|---|---:|---:|---:|---:|---|
| Nano, printed voucher PDF | 512 MiB | None | 6.7 s | 404 MiB | Correct match |
| Nano, addendum PDF | 512 MiB | None | 6.4 s | 402 MiB | Correct addendum |
| Nano, rotated image | 512 MiB | None | 6.3 s | 432 MiB | Correct match |
| Nano, perspective + dim + blur + JPEG | 512 MiB | None | 6.1 s | 423 MiB | Correct match |
| Nano, 12-megapixel image | 512 MiB | None | 6.2 s | 446 MiB | Correct match |
| Nano, 40-page PDF | 512 MiB | None | 32.9 s | 453 MiB | Correct match |
| Small, 12-megapixel image | 512 MiB | None | 5.6 s | 512 MiB | Decoder OOM; PDF retained |
| Small, same image | 512 MiB | 512 MiB | 7.5 s | 512 MiB | Correct match; **28 MiB swap used** |
| Small, same image | 768 MiB | None | 6.7 s | 535 MiB | Correct match |

Blank images received timestamp names. Two distinct QR IDs across a combined PDF
produced an ambiguity flag and no association. Repeated copies of the same ID
matched once. A deliberately tight 384 MiB nano run also retained the upload when
decoding failed. Repeat nano uploads took 5.8–7.0 seconds with similar memory use.

The small-model failure was confirmed with the kernel cgroup OOM-kill counter;
the swap-enabled repeat had zero OOM kills. Raw results are in [results/](results/).

Local host: Intel i5-6600T, 16 GB RAM, Podman 4.9.3. CPU Torch 2.8.0, QReader 3.16,
QRDet 2.5; complete installed versions are in `environment.lock.txt`. The image
is approximately 1.84 GiB and contains both models. The final measurement image
ID starts `9bdeec37548a`; earlier successful runs used equivalent application/model
code before the OOM-counter instrumentation and a download-name compatibility fix.

Times exclude image building, model download, container startup, and fixture
seeding. Host filesystem caches may be warm. The RAM figure is cgroup charged
memory, not summed process RSS; shared mapped pages make those different measures.
Local CPU timings are not a prediction of latency on DigitalOcean.

## Physical scanner check

The printed dummy voucher was signed/annotated by the user and scanned through
the Brother DS-640 daemon. The actual scan (one PDF page, 2,550 × 4,200 pixels,
486,917 bytes) passed the same real HTTP/Gunicorn test inside Podman:

- Nano model, 512 MiB RAM limit, swap disabled.
- Decoded exactly `FY27-0002`, named the test upload `FY27-0002.pdf`, and matched
  the temporary fixture's signout without filing or approving it.
- Upload time **5.722 seconds**; peak container RAM **435.8 MiB**; zero swap,
  zero OOM kills, healthy app afterward, and no decoder left running.

Source: `~/Scans/Inbox/scan-20260914-164350-281238-page-0001.pdf`.
SHA-256: `eeff84b1c9c5d7e42089fd52d0b304e56beb83fc140f06a799a564bcd736fe24`.
The original scan was left unchanged; the test used a copy and a temporary
database. The signed scan itself is not stored in Git. Metrics are in
[real-scanner-nano-512m.jsonl](results/real-scanner-nano-512m.jsonl).

## Decision and remaining checks

The branch defaults to nano. It leaves useful headroom within a 512 MiB app
budget for the tested inputs. The small model is also an option with swap; these
synthetic samples do not establish an accuracy advantage between the two.
Before deployment, test several **real photographs of printed vouchers**,
including an addendum, an angled shot, and dim lighting.

Recognition is synchronous: the existing single Gunicorn worker is occupied
for the duration of an upload, up to the 45-second decoder deadline plus normal
upload processing. Busy/failed scans do not retry automatically. For occasional
uploads this may be acceptable, but it is part of the deployment decision.
The 40-page test is repeated vector pages, not forty full-resolution photos.

The VM read-only check showed 956 MiB total RAM, 609 MiB available at that moment,
no swap, and 20 GB free on an ext4 root filesystem. **Swap can be configured over
SSH as root; no DigitalOcean dashboard action is required.** A 1–2 GiB host swap
file is a reasonable option to consider. Podman's combined memory/swap allowance
must also exceed its RAM limit for the container to use it. The host procedure is
documented by [DigitalOcean](https://www.digitalocean.com/community/tutorials/how-to-add-swap-space-on-ubuntu-22-04).
Nothing was enabled on the VM during this experiment.

## Validation

All existing tests were retained. The backend run initially passed 275 cases and
found two schema-version expectations that needed updating from 10 to 11. Both
were corrected and their suites passed on rerun; the new direct-expense case also
passed (278 backend cases covered across these runs). The 16 QR tests cover exact
IDs, naming, association, duplicates, void/filed/unknown targets, fallback,
serialization, and unchanged accounting state.

Browser coverage: 42 passed on the main run, including both new QR review cases.
One unrelated mobile receipt-editor test failed during the concurrent image build
and passed on its isolated rerun, giving 43 passing browser cases. Two previously
known language-login URL-glob failures were excluded from this run and remain
unchanged. This is not a claim that the entire browser suite is green.

Both actual print routes produced one-page sample PDFs, and their QR codes were
decoded in the container benchmarks. JavaScript syntax and `git diff --check`
also passed. Reproduction commands and implementation limits are in [README.md](README.md).

## QR rotation implementation and generic-receipt orientation probe

QR-driven rotation is now implemented. Six real-scan cases passed through the
full Podman/Gunicorn capture path with nano, 512 MiB RAM, and swap disabled:
pixels rotated clockwise 0/90/180/270 degrees, a four-page combination of those
orientations, and a PDF whose existing rotation metadata was 90 degrees. The
saved PDFs were independently decoded and every QR reported `UP`; their exact
compressed scan-image streams matched the inputs. Single-page uploads took about
6 seconds; the four-page document took 7.656 seconds and peaked at 487.7 MiB.
Every run had zero OOM kills and left the server healthy with no decoder running.
See [raw rotation results](results/real-scan-orientation.jsonl).

The worker returns orientation separately from the decoded business ID. It uses
axis-aligned original-page crops, so QReader's internal perspective correction
cannot change the reference direction. Every decoded copy of the same business
ID on a page must supply a consistent orientation. Pages without reliable
orientation, or with conflicting business IDs, stay unchanged. PDF rotation is
applied before the object is published, and its final hash/size are recorded.

Validation: 123 relevant tests passed, including 21 new orientation cases plus
capture, matching, review, and closeout coverage. The tests check existing PDF
rotation, per-page decisions, unchanged image data, invalid worker output,
uncertain/conflicting directions, and saved-object integrity.

For generic receipts, Tesseract 5.5.0's orientation-and-script detection was tested
in a **separate** Podman image with one CPU, 128 MiB RAM, and no swap. It identified
all four orientations of a synthetic 15-line receipt in 0.814–1.148 seconds, with
peak child-process RSS up to 58.1 MiB. Scores were 11.72–12.78. A synthetic
three-line receipt also returned the right directions, but scores fell to
1.21–1.36. Those confidence scores are not percentages. They demonstrate why an
automatic fallback needs a calibrated threshold and representative real receipts.
These figures exclude the cash app, QReader, and PDF rendering; do not treat them
as a combined application memory benchmark. Results are in
[receipt-osd-probe.jsonl](results/receipt-osd-probe.jsonl).

Tesseract can also reject pages containing too few usable characters, as shown
in its [OSD implementation](https://github.com/tesseract-ocr/tesseract/blob/main/src/ccmain/osdetect.cpp).
A dedicated alternative is PaddleOCR's
[document-orientation classifier](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/doc_img_orientation_classification.en.md),
which predicts 0/90/180/270 degrees. Its documented approximately 7 MB model size
is not its process RAM requirement; that runtime has not been benchmarked here.

Recommendation: use the implemented QR correction for our own printed forms.
For generic receipts, evaluate Tesseract on actual receipt scans first, accepting
only confident results; keep low-confidence pages unchanged for review. Run such
a fallback only on demand and preferably after the QR worker exits, to avoid
adding both processes' peak memory. Generic OCR rotation is **not enabled** in
the application by this change. Nothing was deployed to the VM.

## Actual handwritten receipt: no useful OSD signal

The user's new daemon scan was tested at full resolution in all four cardinal
orientations using the same standalone Tesseract 5.5.0 OSD probe. The input was
an uncropped, one-page PDF containing a 2,550 × 4,200 RGB scan (300 DPI), with a
small sideways handwritten receipt and substantial scanner background/blank area.
No contrast enhancement, cropping, or handwriting transcription was applied.
The original was left unchanged. The Podman limit was 512 MiB, one CPU, no swap,
no network, and a read-only filesystem.

Visual inspection confirmed the source requires a 90-degree clockwise turn.
Tesseract got **zero of four** corrective rotations right:

| Receipt position | Correct clockwise turn | Tesseract's turn | Orientation confidence |
|---|---:|---:|---:|
| Upright | 0° | 180° | 0.35 |
| 90° clockwise | 270° | 180° | 0.28 |
| Upside down | 180° | 0° | 0.64 |
| 90° counterclockwise (as scanned) | 90° | 270° | 0.32 |

Scores were consistently very low (0.28–0.64, versus roughly 12 on the synthetic
text-heavy receipt); they are not percentages. Three of four runs also guessed
Arabic script for the Latin handwriting. The proposed orientation was not even
consistent under known rotations of the same image. Each run took 2.2–2.6 seconds;
maximum child-process RSS was 164.2 MiB. These measurements include the full-size
raster, unlike the smaller synthetic samples used earlier.

This sample provides no reliable Tesseract orientation signal. A confidence
threshold would let us leave it unchanged instead of acting on these guesses.
It does not establish that all handwritten receipts fail, or isolate handwriting
from the effect of the large scanner background. Generic OCR rotation remains
disabled; QR-based orientation is unaffected.

Source filename: `scan-20260914-171143-238762-page-0001.pdf`.
SHA-256: `3179919a4f7b0638e037314c5403dcaeedcb847c82e97b40aee982ee686a7382`.
Only the [metrics and ground-truth comparison](results/handwritten-receipt-osd.jsonl)
are stored in Git; the receipt itself and rotated image copies are not.

## Local Qwen vision comparison on the handwritten receipt

The locally installed Ollama 0.32.15 model `qwen3.5:0.8b` (Q8_0, 873.44M
parameters; digest `f3817196d142eaf72ce79dfebe53dcb20bd21da87ce13e138a8f8e10a866b3a4`)
was given all four rotated copies in one message, labelled by attachment order.
The prompt asked which image was upright and permitted `UNSURE`; filenames,
rotation angles, and ground truth were not sent to the model. Thinking was
disabled, temperature was zero, and the model was unloaded after the request.

The initial full-resolution attempt was cancelled after approximately 4 minutes
24 seconds while processing images, without an answer. A retry reduced each
image's longest edge to 1,024 pixels (622 × 1,024 or 1,024 × 622), retaining the
entire scanner background, and encoded JPEG at quality 85 (about 36 KB each).
The retry also reduced context allocation from 16,384 to 8,192 tokens. These
changes were not independently benchmarked, so the speed improvement cannot be
attributed solely to resizing or JPEG compression.

The resized request completed in **26.406 seconds**, including 3.426 seconds of
model loading, and answered **A**: the upside-down copy. The correct answer was
**B** (90° clockwise from the source scan). This is one failed four-image choice,
not four independent orientation predictions. Ollama reported a maximum loaded
allocation of 1,141.5 MiB; that is not process RSS or a Podman memory measurement.
This run used the existing local CPU Ollama service, outside Podman, and does not
establish suitability for the 1 GiB VM. The model was confirmed unloaded afterward.

Reproduce with the locally generated full-resolution PNG copies:

```sh
.venv/bin/python -m experiments.qr.benchmark_ollama \
  /tmp/cash27-handwritten-osd \
  experiments/qr/results/handwritten-receipt-qwen-1024.json --max-edge 1024
```

The [raw result](results/handwritten-receipt-qwen-1024.json) includes the prompt,
attachment mapping, transmitted dimensions, and Ollama timing fields, without
the receipt images. No generic orientation fallback was enabled.

For the next feasibility test, a dedicated four-class orientation classifier is
a stronger resource-fit candidate: PaddleOCR's documented
[PP-LCNet_x1_0_doc_ori](https://www.paddleocr.ai/main/en/version3.x/module_usage/doc_img_orientation_classification.html)
has approximately 7 MB of weights. Its published 99.06% accuracy is on the
authors' 1,000-image document/ID dataset, not this handwritten sample.
[RapidOrientation](https://github.com/RapidAI/RapidOrientation) also packages a
PaddleClas-derived four-direction ONNX model of approximately 6.5 MB; it should
not be assumed identical to the current PaddleOCR checkpoint. Neither candidate
has yet been benchmarked here. Model file size is not runtime RAM usage.

## Paddle document orientation: fast, but unreliable on this handwriting

Tested the **official PaddlePaddle ONNX export** of `PP-LCNet_x1_0_doc_ori`,
revision `7330ab7039123e46af2dc03154b9969aa412c61d`, with CPU ONNX Runtime
1.30.0, OpenCV 5.0.0.93, and NumPy 2.4.3. The actual model is 6,788,069 bytes
(6.47 MiB), SHA-256
`af9a0a4f317ff0709ce752067807f819cb15d883f8ecad89f28df1c6ee2d9c92`.
The complete standalone image is 448,079,883 bytes (427.3 MiB), including Python,
libraries, and model. This is not the older RapidOrientation checkpoint.

Each image ran in a new Podman container with one CPU, 256 MiB RAM, no swap,
no network, and a read-only filesystem. The script directly uses ONNX Runtime
with preprocessing matching the official `inference.yml` and PaddleX predictor:
RGB, resize short edge to 256 using OpenCV linear interpolation, center crop
224 × 224, ImageNet normalization, and float32 NCHW. PaddleX applies predicted
labels as counterclockwise corrections; this benchmark converts them to
clockwise corrections and independently checks the convention with all four
rotations of a known-upright typed receipt.

| Input | Correct rotations | Fresh-container wall time per image | Peak process RSS | Peak charged container memory |
|---|---:|---:|---:|---:|
| Full handwritten scan | **1/4** | 0.89–1.02 s | 166.6 MiB | 119.5 MiB |
| Manually cropped handwritten receipt | **2/4** | 0.74–0.81 s | 119.2 MiB | 70.2 MiB |
| Synthetic typed receipt control | **4/4** | 0.70–0.74 s | 112.9 MiB | 63.9 MiB |

Inference alone took approximately 6–8 ms. Model loading took 49–58 ms;
Python imports took 167–196 ms. Complete Python process time was 0.26–0.57 s,
including image loading/preprocessing and measurement overhead. Wall time also
includes Podman launch and teardown. Filesystem caches may be warm. RSS includes
mapped shared pages; cgroup memory is charged memory and cache, so these values
are not additive. The recorded cgroup peak is read near process completion.
No memory-limit or OOM events occurred. The cash app, QReader, and PDF rendering
are excluded: these are standalone orientation measurements, not combined app
memory requirements or production-VM timings.

The full-scan case uses exactly the same uncropped PNGs as the Tesseract test.
For diagnosis only, one manually selected bounding box `(2600, 0, 3700, 1750)`
was cut from the upright full-resolution copy (`handwritten-cw-90.png`), then
rotated into the same four orientations. This retains the receipt's handwriting
and removes most scanner background. No automatic cropper or image enhancement
was used. The model still applies its own standard center crop afterward.

| Additional clockwise rotation from original scan | Expected clockwise correction | Full-scan prediction (top score) | Cropped prediction (top score) |
|---|---:|---:|---:|
| 0° | 90° | 270° (0.718) | 270° (0.472) |
| 90° — upright | 0° | 180° (0.585) | 0° (0.885) |
| 180° | 270° | 270° (0.513) | 270° (0.687) |
| 270° — upside down | 180° | 0° (0.866) | 0° (0.528) |

These scores are model outputs, not calibrated probabilities of correctness.
In particular, a wrong full-scan prediction scored 0.866. Cropping helps only
partially and does not establish reliable automatic rotation. One receipt and
one synthetic control are insufficient to select a general confidence threshold.
The model's resource cost is promising; its accuracy on this worst-case receipt
is not sufficient for enabling a generic automatic fallback. No application
behavior, original scan, or production deployment changed.

Raw results:
[full scan](results/handwritten-receipt-paddle-orientation.jsonl),
[manual crop](results/handwritten-receipt-cropped-paddle-orientation.jsonl),
[typed control](results/synthetic-receipt-paddle-orientation.jsonl).
See README for reproduction. Receipt images are kept outside the repository.

References:
[official ONNX checkpoint](https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_doc_ori_onnx/tree/7330ab7039123e46af2dc03154b9969aa412c61d),
[PaddleX classifier preprocessing](https://github.com/PaddlePaddle/PaddleX/blob/release/3.3/paddlex/inference/models/image_classification/predictor.py),
[rotation implementation](https://github.com/PaddlePaddle/PaddleX/blob/release/3.3/paddlex/inference/pipelines/components/common/warp_image.py).

## Second real receipt: printed form with handwriting

The next daemon scan, `scan-20260914-173512-814727-page-0001.pdf`, contains a
printed receipt form with handwritten entries. SHA-256:
`4767a96be013310246aa6a6f94cb21423911dc820db48da93919c4e5e2ef68c9`.
It is a one-page PDF, rotation metadata 0, with one 2,550 × 4,200 RGB image.
Visual inspection establishes that the source needs **270° clockwise** (90°
counterclockwise) to be upright. The embedded raster was extracted using pypdf,
with four lossless PNG rotations stored outside the repository.

The same official ONNX model, preprocessing, and fresh-container limits were
used: one CPU, 256 MiB RAM, no swap, no network. The full scan scored **3/4**.
A diagnostic manual crop `(0, 0, 2550, 1650)` from the as-scanned image removed
the large blank bottom area while retaining the receipt, then was rotated into
the same four directions. The cropped version also scored **3/4**, but missed
a different orientation:

| Additional clockwise rotation from source | Expected clockwise correction | Full-scan prediction (top score) | Cropped prediction (top score) |
|---|---:|---:|---:|
| 0° — as scanned | 270° | **90° (0.783), wrong** | 270° (0.844) |
| 90° — upside down | 180° | 180° (0.480) | 180° (0.894) |
| 180° | 90° | 90° (0.856) | 90° (0.912) |
| 270° — upright | 0° | 0° (0.928) | **180° (0.828), wrong** |

Full-scan wall time was 0.93–1.09 seconds per fresh container, with peak charged
container memory 118.4 MiB and peak process RSS 166.0 MiB. Cropped runs took
0.82–0.89 seconds, peaking at 81.2 MiB charged memory and 128.5 MiB process RSS.
Inference itself took 5.8–8.6 milliseconds. No memory-limit or OOM events occurred.
The same standalone/cached-filesystem measurement caveats above apply.

This is better than the first handwritten receipt, but still not reliable in
all four directions. Cropping fixed the as-scanned case while making the
upright case fail, so it did not improve overall accuracy for this sample.
The scores are not calibrated correctness probabilities. The original PDF's
hash was verified unchanged after testing. No automatic fallback was enabled.

Raw results: [full scan](results/receipt-173512-paddle-orientation.jsonl),
[manual crop](results/receipt-173512-cropped-paddle-orientation.jsonl).
Reproduce against the generated image directories using:

```sh
.venv/bin/python -m experiments.qr.run_orientation_benchmarks \
  /tmp/cash27-receipt-173512 experiments/qr/results/receipt-173512-paddle-orientation.jsonl \
  --prefix receipt2 --source-correction 270
.venv/bin/python -m experiments.qr.run_orientation_benchmarks \
  /tmp/cash27-receipt-173512-cropped experiments/qr/results/receipt-173512-cropped-paddle-orientation.jsonl \
  --prefix receipt2 --source-correction 270
```


## Asynchronous production worker — September 16, 2026

Tested the current production Containerfile with two separate Podman containers,
using disposable data and PDFs/photos generated from the actual dummy voucher
print route. The web container retained its single synchronous Gunicorn worker.
The document worker used `--cpus=0.5`, `--memory=512m`, read-only root filesystem,
128 MiB temporary storage, and no network. Both containers used Podman's default
swap allowance (512 MiB RAM plus up to 512 MiB swap). Kernel cgroup readings
confirmed `cpu.max=50000 100000`, `memory.max=536870912`, and
`memory.swap.max=536870912`; the CPU quota was actively throttling the worker.
The installed production Compose provider (1.5.0) was checked in a local dry run:
CPU/RAM/PID limits, network isolation, and health checks are forwarded.
`memswap_limit` is not forwarded by that provider, so the deployment relies on
Podman's verified default instead.

| Input | HTTP acceptance time |
|---|---:|
| Actual dummy voucher PDF | 0.138 s |
| Same voucher upside down | 0.016 s |
| 12-megapixel simulated phone photo | 0.180 s |

All three uploads returned with durable queued jobs. They finished serially in
37.767 seconds total, with one matched voucher and two duplicate-scan
suggestions. The backwards PDF was independently verified upright after
processing. Across 288 concurrent ledger/health requests, p95 latency was
0.008 seconds and maximum latency was 0.011 seconds. Worker peak cgroup memory
was 387.6 MiB, with zero OOM or OOM-kill events. Idle cgroup
memory was 20.2 MiB before jobs and 38.5 MiB afterward; the models exited
between jobs. The image remains approximately 2.03 GB and is shared by app,
worker, and archive containers.

These are local-machine measurements under container limits, not timings from
the 1 GB production VM. No host swap was provisioned and no production
application was changed in this experiment. Host swap still needs the committed
setup provisioning during deployment.

Automated checks also cover scanner retry idempotency, no database lock during
model execution, worker interruption/recovery, deletion while running, filing
rejection while queued, migration of existing documents, database-import
coordination, and backups restored before/after rotation. Browser tests cover
processing-to-ready and failure states, phone layout, incoming scanner documents,
and preservation of unsaved review coding and focus.

Raw results: `results/async-capture.json`.

```sh
podman build --layers --target app -t localhost/cash27-async-check .
.venv/bin/python -m experiments.qr.benchmark_async \
  --image localhost/cash27-async-check \
  --output experiments/qr/results/async-capture.json
```
