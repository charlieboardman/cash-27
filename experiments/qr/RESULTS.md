# Server-side QR: local decision report

**Server-side decoding looks viable on the existing VM.** The nano model passed
all nine initial cases under Podman at 512 MiB RAM with swap disabled. The larger
small model needed a little more room for a 12-megapixel image; allowing swap
made that upload succeed. These are feasibility results, not a claim that every
phone photo will decode.

Developed on `feature/server-qr-capture`, September 14, 2026. Production and the
scanner daemon were not changed. No swap was configured on the VM.

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
- Keeps recognition off unless `CASH_QR_ENABLED=1` is set. The normal deployment
  image has not been changed to include the optional ML dependencies.

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
