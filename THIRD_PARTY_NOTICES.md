# Third-party notices

The root [MIT license](LICENSE) applies to Cash 27's original code and
documentation. Third-party software and model weights retain their respective
copyrights and licenses. This file indexes the main components; their complete
license texts and bundled notices remain authoritative.

## Vendored browser library

**fzf for JavaScript 0.5.2**, Copyright (c) 2021 Ajit, is distributed unmodified
under BSD-3-Clause. Its [license](cash/static/vendor/fzf/LICENSE.txt) and
[source/integrity information](cash/static/vendor/fzf/README.md) accompany the
JavaScript in this repository and the application image.

## Receipt-orientation model

**PP-LCNet_x1_0_doc_ori_onnx**, published by PaddlePaddle, is declared
Apache-2.0 in its [model card](https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_doc_ori_onnx/blob/7330ab7039123e46af2dc03154b9969aa412c61d/README.md).
The build downloads the unmodified ONNX weights from that pinned revision and
verifies their SHA-256. The [Apache license](licenses/paddle-document-orientation/LICENSE),
unmodified [model card](licenses/paddle-document-orientation/MODEL_CARD.md), and
[provenance](licenses/paddle-document-orientation/README.md) accompany the model
under `/opt/cash27-orientation/` in the production image.

## Python runtime dependencies

Dependencies are installed from [requirements.txt](requirements.txt) and
[requirements-capture.txt](requirements-capture.txt). Preserve their installed
license and notice files when redistributing an installation or container.

| Component | Upstream | License |
|---|---|---|
| Flask | [pallets/flask](https://github.com/pallets/flask) | BSD-3-Clause |
| Gunicorn | [benoitc/gunicorn](https://github.com/benoitc/gunicorn) | MIT |
| openpyxl | [openpyxl](https://openpyxl.readthedocs.io/) | MIT |
| Pillow | [python-pillow/Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU; bundled libraries have additional notices |
| pypdf | [py-pdf/pypdf](https://github.com/py-pdf/pypdf) | BSD-3-Clause |
| qrcode | [lincolnloop/python-qrcode](https://github.com/lincolnloop/python-qrcode) | BSD |
| tzdata | [python/tzdata](https://github.com/python/tzdata) | Apache-2.0; IANA time-zone data has its own public-domain notice |
| ZXing-C++ | [zxing-cpp/zxing-cpp](https://github.com/zxing-cpp/zxing-cpp) | Apache-2.0 |
| pypdfium2 / PDFium | [pypdfium2-team/pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | Apache-2.0 or BSD-3-Clause for the bindings; PDFium and bundled components have additional licenses |
| OpenCV | [opencv/opencv-python](https://github.com/opencv/opencv-python) | Apache-2.0 for OpenCV; packaging and bundled libraries have additional licenses |
| NumPy | [numpy/numpy](https://github.com/numpy/numpy) | BSD-3-Clause and additional bundled-component licenses |
| ONNX Runtime | [microsoft/onnxruntime](https://github.com/microsoft/onnxruntime) | MIT and bundled third-party notices |

Transitive dependencies and development tools also retain their own licenses.
For the exact installed versions, use `python -m pip list` inside the relevant
environment. The historical QR experiment lock file describes an earlier
dependency set, not the current runtime.

## OpenCV bundled libraries

The installed `opencv-python` wheel includes FFmpeg under LGPL-2.1 and, in
the non-headless Linux wheel used here, Qt 5 under LGPL-3.0. The wheel carries
their license texts in `cv2/LICENSE-3RD-PARTY.txt`; see the upstream
[licensing information](https://github.com/opencv/opencv-python#licensing).
ONNX Runtime likewise carries `onnxruntime/ThirdPartyNotices.txt`, and PDFium
ships its third-party license inventory with pypdfium2.

Redistributing these binaries requires following their applicable terms,
including LGPL source-availability and library replacement/relinking provisions
where applicable. This inventory and retained license texts alone do not
replace those requirements. The container's Python base image and operating
system packages also retain their own licenses and distribution notices.
