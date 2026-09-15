"""One-shot CPU receipt orientation; four-view agreement or leave unchanged."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path


def agreed_correction(predictions, threshold=0.90):
    """Each prediction is (label, score) for 0/90/180/270 CW input views.

    Paddle labels are counterclockwise corrections. Undo each test-view rotation
    when mapping predictions to the original page. Scores are not probabilities
    of correctness; agreement and the threshold are an initial abstention rule.
    """
    if len(predictions) != 4:
        return None
    corrections = []
    for offset, (label, score) in zip((0, 90, 180, 270), predictions):
        if label not in (0, 90, 180, 270) or not threshold <= score <= 1:
            return None
        corrections.append((offset - label) % 360)
    return corrections[0] if len(set(corrections)) == 1 else None


def orient_pages(path, *, weights, pages):
    os.environ.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    import cv2
    import numpy as np
    import onnxruntime as ort
    import pypdfium2 as pdfium

    cv2.setNumThreads(1)
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(weights), sess_options=options,
                                   providers=['CPUExecutionProvider'])
    input_name = session.get_inputs()[0].name
    alpha = np.array([1 / 255 / s for s in (.229, .224, .225)], dtype=np.float32)
    beta = np.array([-m / s for m, s in zip((.485, .456, .406), (.229, .224, .225))], dtype=np.float32)
    with contextlib.closing(pdfium.PdfDocument(path)) as pdf:
        if not 1 <= len(pdf) <= 40 or any(type(i) is not int or not 0 <= i < len(pdf) for i in pages):
            raise ValueError('Invalid receipt page selection')
        rotations = [None] * len(pdf)
        decisions = []
        for index in sorted(set(pages)):
            with contextlib.closing(pdf[index]) as page:
                width, height = page.get_size()
                if min(width, height) <= 0:
                    raise ValueError('Invalid page dimensions')
                bitmap = page.render(scale=min(200 / 72, 1600 / max(width, height)))
                try:
                    image = np.asarray(bitmap.to_pil().convert('RGB'))
                    # Extreme strips carry little useful text and would expand
                    # excessively during the model's short-side resize.
                    if max(image.shape[:2]) / min(image.shape[:2]) > 20:
                        decisions.append({'page': index, 'predictions': [], 'clockwise': None})
                        continue
                    predictions = []
                    for turn in range(4):
                        view = np.rot90(image, -turn)
                        h, w = view.shape[:2]
                        scale = 256 / min(h, w)
                        resized = cv2.resize(view, (round(w * scale), round(h * scale)),
                                             interpolation=cv2.INTER_LINEAR)
                        y, x = (resized.shape[0] - 224) // 2, (resized.shape[1] - 224) // 2
                        crop = resized[y:y+224, x:x+224].astype(np.float32)
                        crop *= alpha
                        crop += beta
                        tensor = np.ascontiguousarray(crop.transpose(2, 0, 1)[None])
                        scores = session.run(None, {input_name: tensor})[0][0]
                        label = int(np.argmax(scores))
                        predictions.append((label * 90, float(scores[label])))
                    rotations[index] = agreed_correction(predictions)
                    decisions.append({'page': index, 'predictions': predictions,
                                      'clockwise': rotations[index]})
                    del image
                finally:
                    bitmap.close()
    return {'rotations': rotations, 'decisions': decisions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf')
    parser.add_argument('--weights', required=True, type=Path)
    parser.add_argument('--pages', required=True, help='JSON list of zero-based page indices')
    args = parser.parse_args()
    print(json.dumps(orient_pages(args.pdf, weights=args.weights, pages=json.loads(args.pages))))


if __name__ == '__main__':
    main()
