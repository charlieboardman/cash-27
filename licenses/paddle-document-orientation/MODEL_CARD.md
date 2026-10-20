---
license: apache-2.0
library_name: PaddleOCR
language:
- en
- zh
pipeline_tag: image-to-text
tags:
- OCR
- PaddlePaddle
- PaddleOCR
- doc_img_orientation_classification
---

# PP-LCNet_x1_0_doc_ori

## Introduction

The Document Image Orientation Classification Module is primarily designed to distinguish the orientation of document images and correct them through post-processing. During processes such as document scanning or ID photo capturing, the device might be rotated to achieve clearer images, resulting in images with various orientations. Standard OCR pipelines may not handle these images effectively. By leveraging image classification techniques, the orientation of documents or IDs containing text regions can be pre-determined and adjusted, thereby improving the accuracy of OCR processing. The key accuracy metrics are as follow:

<table>
<tr>
<th>Model</th>
<th>Recognition Avg Accuracy(%)</th>
<th>Model Storage Size (M)</th>
<th>Introduction</th>
</tr>
<tr>
<td>PP-LCNet_x1_0_doc_ori</td>
<td>99.06</td>
<td>7</td>
<td>A document image classification model based on PP-LCNet_x1_0, with four categories: 0°, 90°, 180°, and 270°.</td>
</tr>
</table>

## Model Usage

### Install Dependencies

```shell
pip install -U paddleocr
pip install -U onnxruntime-gpu
```

### CLI Usage

```shell
paddleocr doc_img_orientation_classification -i ./demo.jpg --model_name PP-LCNet_x1_0_doc_ori --engine onnxruntime
```

### Python API Usage

```python
from paddleocr import DocImgOrientationClassification

model = DocImgOrientationClassification(
    model_name="PP-LCNet_x1_0_doc_ori",
    engine="onnxruntime",
)
output = model.predict("./demo.jpg", batch_size=1)
for res in output:
    res.print()
    res.save_to_json(save_path="./output/res.json")
```