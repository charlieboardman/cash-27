"""One-shot, CPU-only PDF QR decoder. No model stays in the web process."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import resource
import sys
import time

# Match the ID generators, including voucher sequences longer than four digits.
BUSINESS_ID = re.compile(r"FY[0-9]{2}-[0-9]{4,}(?:-A[1-9][0-9]*)?", re.ASCII)


def valid_ids(values):
    return sorted({value for value in values
                   if isinstance(value, str) and BUSINESS_ID.fullmatch(value)})


def correction_for_orientations(orientations):
    """ZBar directions in page coordinates -> clockwise PDF correction."""
    corrections = {"UP": 0, "LEFT": 90, "DOWN": 180, "RIGHT": 270}
    values = [corrections.get(value) for value in orientations]
    return values[0] if values and None not in values and len(set(values)) == 1 else None


def page_correction(image, decoded, detections):
    """Read orientation on axis-aligned crops, never QReader's warped image.

    Require one business ID and agreement from every decoded copy on this page.
    Cropping/scaling preserve the page axes; perspective rectification may not.
    """
    import cv2
    import numpy as np
    from pyzbar.pyzbar import decode, ZBarSymbol

    ids = valid_ids(decoded)
    if len(ids) != 1:
        return None
    orientations = []
    height, width = image.shape[:2]
    for value, detection in zip(decoded, detections):
        if value != ids[0]:
            continue
        x1, y1, x2, y2 = detection["bbox_xyxy"]
        margin = max(8, int(max(x2 - x1, y2 - y1) * .15))
        crop = image[max(0, int(y1) - margin):min(height, int(y2) + margin),
                     max(0, int(x1) - margin):min(width, int(x2) + margin)]
        if not crop.size:
            return None
        results = decode(np.ascontiguousarray(crop), symbols=[ZBarSymbol.QRCODE])
        relevant = [result for result in results if result.data == value.encode("ascii")]
        if not relevant and max(crop.shape[:2]) <= 650:
            results = decode(cv2.resize(crop, None, fx=2, fy=2), symbols=[ZBarSymbol.QRCODE])
            relevant = [result for result in results if result.data == value.encode("ascii")]
        if not relevant:
            return None
        orientations.extend(result.orientation for result in relevant)
    return correction_for_orientations(orientations)


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


def decode_pdf(path, *, weights, model="n"):
    # Set before importing torch/OpenCV; avoid idle thread pools and GPU probing.
    os.environ.update(OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
                      OPENBLAS_NUM_THREADS="1", CUDA_VISIBLE_DEVICES="",
                      YOLO_CONFIG_DIR=os.environ.get("YOLO_CONFIG_DIR", "/tmp/cash27-yolo"))
    import cv2
    import numpy as np
    import pypdfium2 as pdfium
    import torch
    from qreader import QReader

    torch.set_num_threads(1)
    torch.set_num_interop_threads(1)
    cv2.setNumThreads(1)
    reader = QReader(model_size=model, weights_folder=weights, reencode_to=None)
    found = set()
    rotations = []
    qr_pages = []
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
                    image = np.asarray(bitmap.to_pil().convert("RGB"))
                    decoded, detections = reader.detect_and_decode(
                        image=image, return_detections=True)
                    found.update(valid_ids(decoded))
                    qr_pages.append(bool(valid_ids(decoded)))
                    rotations.append(page_correction(image, decoded, detections))
                    del image
                finally:
                    bitmap.close()
    return {"ids": sorted(found), "rotations": rotations, "qr_pages": qr_pages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--model", choices=("n", "s"), default="n")
    args = parser.parse_args()
    start = time.monotonic()
    # Third-party libraries can write informational messages to stdout.
    with contextlib.redirect_stdout(sys.stderr):
        result = decode_pdf(args.pdf, weights=args.weights, model=args.model)
    print(json.dumps({**result, "seconds": round(time.monotonic() - start, 3),
                      "peak_rss_mib": round(resource.getrusage(
                          resource.RUSAGE_SELF).ru_maxrss / 1024, 1), **cgroup_memory()}))


if __name__ == "__main__":
    main()
