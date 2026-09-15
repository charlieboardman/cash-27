# DS-640 driver investigation — 2026-09-14

## Completed daemon follow-up

The successful presence-query approach is now installed separately in
`/home/charlie/brother-ds640-daemon`, version 1.2.0, commit `4ffa1a3` on `main`
(pushed). Its 25 unit tests passed. The installed service then automatically
scanned a different short yellow receipt into
`~/Scans/Inbox/scan-20260914-175742-871970-page-0001.pdf`, with a 1617 × 1254
image, and returned to waiting for paper. Uploading is still a separate task.
The notes below describe the earlier feasibility probe and its state at that
time; they are not the current installed daemon status.

## Follow-up: normal scan triggered by paper presence works

The user clarified that ATCN is an implementation choice, not a requirement.
The investigation therefore tested replacing it with a paper-presence query
followed by an ordinary automatically sized scan. **The live test succeeded.**

The installed backend exports `bssdkwrapper_IsAdfInserted()`. Disassembly shows
that it calls the core's `QueryAdfState()` and returns true for enum value 2.
The underlying method calls `CheckDocument` with the ADF source; it does not
invoke `StartScanning`. The probe calls the underlying method directly so it
can retain the enum rather than treating all errors as an empty feeder.

Live observations on this DS-640:

- Empty feeder: raw state 3; checks took about 3–20 ms.
- Paper inserted: raw state changed to 2 during 0.5-second polling.
- The USB command trace contained discovery `Q`/`QDI` and polling
  `CKD` with `PSRC=ADF`, without an SSP/XSC scan start during polling.
- After detecting paper, the probe closed its SANE handle, then launched
  `scanimage --format=png --batch=... --batch-count=1 --resolution=300
  --AutoDocumentSize=yes` without the ATCN patch.
- The outgoing scan commands explicitly contained **`ATCN=OFF`** and
  **`AREA=ATDSKW`**. The scan exited successfully after **4.115 seconds**.
- The complete PNG is **1619 × 1256 at 300 DPI**, fitted to the receipt instead
  of the previous fixed 2550 × 4200 area. Visual inspection found the receipt
  content present and the large blank tail absent. No manual crop or ML was
  applied. This was a new school receipt, not the prior auto-parts receipt.

The original daemon was stopped only for exclusive device access and restored
in a `finally` block. Its installed code and configuration remain unchanged.
The saved PDF is `~/Scans/Inbox/scan-20260914-174818-normal-autosize.pdf`.
Private raw image and command trace remain under
`~/.local/state/brother-ds640-normal-scan-experiment/20260914-174818/`.
Only [metrics and hashes](results/normal-autosize-scan.json) are in the repository.

Reproduction code: `driver_presence_probe.py` and `run_presence_scan.py`.
The runner temporarily stops the service, watches for insertion for up to three
minutes, runs one normal scan, and restores the service even on failure. Run it
with the scanner daemon's installed Python environment so `ds640_daemon` imports
are available. It is a controlled experiment, not an installed replacement.

Recommended daemon design: open/query the driver while idle, wait for state 2,
release the query handle, perform a normal automatically sized scan, publish
the complete PDF, then return to querying. Device disappearance, query errors,
timeouts/jams, and repeated insertions need explicit handling before replacing
the installed daemon. In particular, treat unknown enum values as errors rather
than assuming "no paper." The undocumented ABI must be tied to this driver
version. One successful scan establishes feasibility, not full lifecycle and
reconnect reliability. The previous ATCN-specific investigation follows.

## Finding

There is a credible route to measuring the received image height before the
driver pads the output, distinct from enabling automatic document sizing. This
is **not yet verified on the DS-640 in ATCN mode**. Static code inspection cannot
establish whether the incoming image already includes the scanner background.
No separate ready-to-use document rectangle was found in the daemon's output.

The scanner service, its configuration, and its installed libraries were left
unchanged and running throughout this investigation. No physical scan was
triggered. An optional passive probe was compiled and checked against a mock
library; it has not been loaded into the scanner process.

## Evidence

Installed driver: brscan5 1.5.1-0, with:

- `libsane-brother5.so.1.0.7`, SHA-256
  `f83cc557dcf64bd097d5a1dfce3670dec32a0b1cf24ad4bbcb6349d1e340718c`.
- `libLxBsScanCoreApi.so.3.2.1`, SHA-256
  `7d79a589ba0b1399f9d400371659f43eb8d394949c1ff9892615977adc994979`.

The daemon's Git history enabled `--AutoDocumentSize=yes` in `e8c9cbe`, then
removed it in `26370d7` because combining it with ATCN stalled image transfer.
The older `auto_document_size` config property is deliberately ignored now.
This is documented local history, not a stall reproduced during this inspection.

`nm -D -C` and `objdump -d -C` on the installed binaries establish:

1. `my_sane_get_parameters` returns either requested scan-area dimensions or
   dimensions stored for the processed page. The function does not expose a
   separate detected-paper rectangle. This is consistent with SANE's standard
   frame parameters, which describe pixel dimensions and row count.
2. The core contains `ParseActualParameterData` and
   `ParsePaperSizeDetectedData`, plus one-pass/two-pass auto-crop paths. These
   are shared driver capabilities, not proof that this device emits physical
   paper boundaries in the current mode.
3. `DeviceImageLines::GetHeight()` at `0x8a0f0` computes its height from the
   number of received line entries. `DeviceImageJpeg::GetHeight()` at `0x8b080`
   reads the JPEG header height. Both precede final image processing in the
   driver's data model, but may still describe background-containing rasters.
4. `DecodeParameter::WillAppendHeight()` at `0xb4fc0` evaluates
   `HasHeight() && !GetDetectEndOfPage()`. `GetRequiredHeightForScanDecode`
   consumes that decision. The driver also has
   `ShouldAppendLengthToEndOfPage`, called by both v1 and v2 image-enhancement
   parameter conversion. Thus padding/end-of-page behavior is separately
   represented internally; it is not simply synonymous with auto-cropping.
5. The SANE helper `get_current_autocrop_setting` returns zero in this build,
   while `get_current_sizeDetect_setting` reads a configuration field. This
   cautions against treating all similarly named internal flags as the same
   feature or assuming they are exposed as user options.

The active session's `page-0001.png.part` was inspected read-only. Its PNG header
declares 2550 × 4200 RGB pixels, with only IHDR/pHYs/IDAT chunks observed. Its
available compressed stream expands to over 4,123 rows, far beyond the roughly
1,650 rows occupied by the latest receipt. It lacks the final PNG trailer while
ATCN waits, as the daemon already documents. Therefore the blank region is
already substantially present in the image stream; reading PNG dimensions or
merely counting available rows will not recover this receipt's paper boundary.

## Next physical test

`driver_dimensions_probe.c` passively forwards these exported methods and logs
only numeric dimensions/flags, with a 4,096-event cap. It preserves their return
values and arguments. Compile with:

```sh
cc -shared -fPIC -Wall -Wextra -Werror \
  -o /tmp/cash27-driver-dimensions-probe.so \
  experiments/qr/driver_dimensions_probe.c -ldl
```

The smoke check used a mock shared library with known getter returns and a
setter, confirming unchanged values and emitted logs. That validates basic
interposition, not compatibility with a live scan or coverage of every driver
call path.

For a controlled rescan, prepend this library to the existing ATCN shim in the
scan subprocess's `LD_PRELOAD` and set `DS640_DIMENSIONS_LOG` to a private path
writable inside the daemon's bubblewrap environment. Use a temporary wrapper
or isolated run, not a permanent installation. The existing runtime overwrites
`LD_PRELOAD`, so setting it only in the parent environment will not work.
Do not run another scanner client concurrently with the active daemon.

Compare received height with requested decode height and final 4,200-row output
on a short receipt. A short received height plus enabled padding would support
retaining that dimension as metadata or testing end-of-page processing without
auto-size. A received height already equal to the full area would require a
deeper protocol trace or software boundary detection. Width and full corner
coordinates require separate verification even if true feed length is available.

No crop or padding behavior should be changed based on static symbols alone.

References:
[SANE frame-parameter specification](https://www.sane-project.org/html/doc012.html),
[Brother Linux auto-size documentation](https://support.brother.com/g/b/faqend.aspx?c=us_ot&faqid=faq00100711_000&lang=en&prod=ds640_us_eu_as).
