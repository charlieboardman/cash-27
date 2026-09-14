"""Synthetic receipts for the isolated OSD feasibility check."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 28)
lines = ['SAMPLE SHOP', '123 Example Street', 'September 14, 2026', 'Receipt number 987654',
         'Classroom notebooks     12.00', 'Colored pencils          6.50',
         'Drawing paper            4.00', 'Glue sticks              3.00',
         'Subtotal                25.50', 'Tax                      2.04',
         'Total                   27.54', 'Cash paid               30.00',
         'Change                   2.46', 'Thank you for shopping with us',
         'Keep this receipt for your records']
for kind, text in [('receipt', lines), ('sparse', ['SAMPLE SHOP', 'Total: 27.54', 'Paid in cash'])]:
    image = Image.new('RGB', (700, 900), 'white')
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(text):
        draw.text((40, 30 + i * 48), line, fill='black', font=font)
    for angle in (0, 90, 180, 270):
        image.rotate(-angle, expand=True).save(args.output / f'{kind}-cw-{angle}.png', dpi=(200, 200))
