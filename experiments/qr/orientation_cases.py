"""Build rotated copies of a real scan, leaving its original untouched."""
import argparse
from pathlib import Path
from PIL import Image
from pypdf import PdfReader, PdfWriter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('scan', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    original = PdfReader(args.scan).pages[0].images[0].image.convert('RGB')
    transforms = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
                  270: Image.Transpose.ROTATE_90}
    for angle in (0, 90, 180, 270):
        image = original if angle == 0 else original.transpose(transforms[angle])
        image.save(args.output / f'pixels-cw-{angle}.pdf', 'PDF', resolution=300)
    writer = PdfWriter()
    for angle in (0, 90, 180, 270):
        writer.append(args.output / f'pixels-cw-{angle}.pdf')
    with (args.output / 'mixed-four-directions.pdf').open('wb') as output:
        writer.write(output)
    writer = PdfWriter()
    writer.add_page(PdfReader(args.scan).pages[0].rotate(90))
    with (args.output / 'metadata-cw-90.pdf').open('wb') as output:
        writer.write(output)
    print(args.output)


if __name__ == '__main__':
    main()
