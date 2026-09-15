"""Ask local Ollama to choose the upright image from four rotated scan copies."""
import argparse
import base64
import json
import io
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument('cases', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--max-edge', type=int, default=1024)
args = parser.parse_args()
# Present an arbitrary order, without revealing angles or ground truth to the model.
angles = [270, 90, 0, 180]
images = []
image_metadata = []
for angle in angles:
    with Image.open(args.cases / f'handwritten-cw-{angle}.png') as source:
        original_size = source.size
        resized = source.convert('RGB')
        resized.thumbnail((args.max_edge, args.max_edge), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, format='JPEG', quality=85)
        images.append(base64.b64encode(buffer.getvalue()).decode())
        image_metadata.append({'source_dimensions': original_size,
                               'sent_dimensions': resized.size,
                               'jpeg_bytes': buffer.tell()})
prompt = (
    'The four attached images show the exact same handwritten receipt at different rotations. '
    'In attachment order, label them A, B, C, and D. Which ONE image has the handwriting '
    'right-side-up, readable normally without tilting your head? Judge the direction of the '
    'handwriting, not the portrait/landscape shape of the image or where the paper is located. '
    'Reply with the letter and one brief sentence explaining your choice. If you cannot tell, '
    'reply UNSURE. Do not transcribe the receipt.'
)
payload = {
    'model': 'qwen3.5:0.8b',
    'messages': [{'role': 'user', 'content': prompt,
                  'images': images}],
    'stream': False, 'think': False, 'keep_alive': 0,
    'options': {'temperature': 0, 'num_predict': 128, 'num_ctx': 8192},
}
stop = threading.Event()
loaded_sizes = []
def monitor():
    while not stop.wait(2):
        try:
            with urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=2) as response:
                for model in json.load(response).get('models', []):
                    if model['name'] == payload['model']:
                        loaded_sizes.append(model.get('size', 0))
        except (OSError, urllib.error.URLError):
            pass
thread = threading.Thread(target=monitor, daemon=True)
thread.start()
start = time.monotonic()
try:
    request = urllib.request.Request('http://127.0.0.1:11434/api/chat',
                                     data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=600) as response:
        result = json.load(response)
finally:
    stop.set()
    thread.join(timeout=3)
record = {
    'model': payload['model'], 'prompt': prompt,
    'attachment_order_clockwise_from_scan': dict(zip('ABCD', angles)),
    'expected_choice': 'B', 'seconds': round(time.monotonic()-start, 3),
    'preprocessing': {'max_edge': args.max_edge, 'jpeg_quality': 85, 'cropped': False,
                      'images': image_metadata},
    'options': payload['options'], 'think': False, 'keep_alive': 0,
    'max_ollama_reported_loaded_mib': round(max(loaded_sizes, default=0) / 2**20, 1),
    'response': result,
}
args.output.write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
