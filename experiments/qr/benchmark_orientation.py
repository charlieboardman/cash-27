"""Single-image, cold-process probe of the official Paddle document classifier.

Preprocessing follows the checkpoint's inference.yml and PaddleX release/3.3
image_classification predictor: RGB, resize short side 256 (OpenCV linear),
224-square center crop, ImageNet normalization, NCHW float32.
"""
import time

START = time.perf_counter()
import argparse
import hashlib
import json
from pathlib import Path
import resource

import cv2
import numpy as np
import onnxruntime as ort


def cgroup_value(name):
    path = Path('/sys/fs/cgroup') / name
    return path.read_text().strip() if path.exists() else None


parser = argparse.ArgumentParser()
parser.add_argument('image', type=Path)
parser.add_argument('--expected-cw', type=int, required=True)
parser.add_argument('--preview', type=Path)
args = parser.parse_args()
cv2.setNumThreads(1)
imports_seconds = time.perf_counter() - START
options = ort.SessionOptions()
options.intra_op_num_threads = 1
options.inter_op_num_threads = 1
load_start = time.perf_counter()
session = ort.InferenceSession('/model/inference.onnx', sess_options=options,
                               providers=['CPUExecutionProvider'])
load_seconds = time.perf_counter() - load_start
prep_start = time.perf_counter()
img = cv2.imread(str(args.image))
if img is None:
    raise ValueError('Could not load image')
height, width = img.shape[:2]
scale = 256 / min(height, width)
resized = cv2.resize(img, (round(width * scale), round(height * scale)),
                     interpolation=cv2.INTER_LINEAR)
y = (resized.shape[0] - 224) // 2
x = (resized.shape[1] - 224) // 2
crop = resized[y:y+224, x:x+224]
if args.preview:
    cv2.imwrite(str(args.preview), crop)
rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)
rgb *= np.array([1 / 255 / v for v in (.229, .224, .225)], dtype=np.float32)
rgb += np.array([-m / s for m, s in zip((.485, .456, .406), (.229, .224, .225))], dtype=np.float32)
tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])
preprocess_seconds = time.perf_counter() - prep_start
infer_start = time.perf_counter()
scores = session.run(None, {session.get_inputs()[0].name: tensor})[0][0]
inference_seconds = time.perf_counter() - infer_start
label = int(np.argmax(scores)) * 90
# PaddleX applies this label with cv2.getRotationMatrix2D's positive CCW angle.
clockwise_correction = (-label) % 360
record = {
    'case': args.image.name, 'model': 'PP-LCNet_x1_0_doc_ori',
    'model_revision': '7330ab7039123e46af2dc03154b9969aa412c61d',
    'model_sha256': hashlib.sha256(Path('/model/inference.onnx').read_bytes()).hexdigest(),
    'model_bytes': Path('/model/inference.onnx').stat().st_size,
    'versions': {'onnxruntime': ort.__version__, 'opencv': cv2.__version__, 'numpy': np.__version__},
    'source_dimensions': [width, height], 'model_input_dimensions': [224, 224],
    'label': label, 'scores': dict(zip(['0', '90', '180', '270'], map(float, scores))),
    'expected_clockwise_correction': args.expected_cw,
    'predicted_clockwise_correction': clockwise_correction,
    'correct': clockwise_correction == args.expected_cw,
    'import_seconds': imports_seconds, 'model_load_seconds': load_seconds,
    'preprocess_seconds': preprocess_seconds, 'inference_seconds': inference_seconds,
    'process_seconds': time.perf_counter() - START,
    'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
    'cgroup_peak_bytes': cgroup_value('memory.peak'),
    'cgroup_memory_events': cgroup_value('memory.events'),
}
print(json.dumps(record))
