"""One-shot, CPU-only PDF QR decoder. No decoder stays in the web process."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
from pathlib import Path
import resource
import sys
import time

from .document_codes import parse


def valid_ids(values):
    """Exactly the codes the app prints (document_codes)."""
    return sorted({value for value in values if parse(value)})


def correction_for_orientations(orientations):
    """ZXing image-coordinate degrees -> clockwise PDF correction.

    Tolerate ordinary camera skew, but abstain near diagonal orientations and
    whenever decoded copies disagree. Never use a perspective-rectified crop.
    """
    corrections = []
    for angle in orientations:
        if type(angle) not in (int, float) or not math.isfinite(angle):
            return None
        cardinal = round(angle / 90) * 90
        if abs(angle - cardinal) > 30:
            return None
        corrections.append(-cardinal % 360)
    return corrections[0] if corrections and len(set(corrections)) == 1 else None


def page_correction(results):
    """Require one business ID and agreement from every decoded copy."""
    ids = valid_ids(result.text for result in results)
    if len(ids) != 1:
        return None
    return correction_for_orientations(
        result.orientation for result in results if result.text == ids[0])


def cgroup_memory():
    """Linux cgroup peak includes the app, decoder, and charged file cache."""
    try:
        relative = next(line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines()
                        if line.startswith("0::"))
        root = Path("/sys/fs/cgroup") / relative.lstrip("/")
        events = dict(line.split() for line in (root / "memory.events").read_text().splitlines())
        return {"cgroup_peak_mib": round(int((root / "memory.peak").read_text()) / 2**20, 1),
                "cgroup_swap_peak_mib": round(int((root / "memory.swap.peak").read_text()) / 2**20, 1),
                "oom_kills": int(events.get("oom_kill", "0"))}
    except (OSError, ValueError, StopIteration):
        return {}


def decode_pdf(path):
    import pypdfium2 as pdfium
    import zxingcpp

    found = set()
    rotations = []
    qr_pages = []
    page_ids = []
    with contextlib.closing(pdfium.PdfDocument(path)) as pdf:
        if not 1 <= len(pdf) <= 40:
            raise ValueError("PDF page limit")
        for index in range(len(pdf)):
            with contextlib.closing(pdf[index]) as page:
                width, height = page.get_size()
                if min(width, height) <= 0:
                    raise ValueError("Invalid page dimensions")
                scale = min(200 / 72, 2600 / max(width, height))
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil().convert("RGB")
                    results = zxingcpp.read_barcodes(
                        image, formats=zxingcpp.BarcodeFormat.QRCode, is_pure=False)
                    ids = valid_ids(result.text for result in results)
                    found.update(ids)
                    qr_pages.append(bool(ids))
                    page_ids.append(ids)
                    rotations.append(page_correction(results))
                    del image
                finally:
                    bitmap.close()
    return {"ids": sorted(found), "rotations": rotations, "qr_pages": qr_pages,
            "page_ids": page_ids}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    args = parser.parse_args()
    start = time.monotonic()
    # Third-party libraries can write informational messages to stdout.
    with contextlib.redirect_stdout(sys.stderr):
        result = decode_pdf(args.pdf)
    print(json.dumps({**result, "seconds": round(time.monotonic() - start, 3),
                      "peak_rss_mib": round(resource.getrusage(
                          resource.RUSAGE_SELF).ru_maxrss / 1024, 1), **cgroup_memory()}))


if __name__ == "__main__":
    main()
