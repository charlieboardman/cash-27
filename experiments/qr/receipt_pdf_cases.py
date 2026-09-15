"""Make private four-orientation PDFs with independently supplied ground truth."""
import argparse
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--source-correction', required=True, type=int, choices=(0, 90, 180, 270))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    original = PdfReader(args.source)
    assert len(original.pages) == 1, 'This probe expects one visually inspected receipt'
    expected = (original.pages[0].rotation + args.source_correction) % 360
    for angle in (0, 90, 180, 270):
        page = PdfReader(args.source).pages[0]
        writer = PdfWriter()
        writer.add_page(page.rotate(angle))
        target = args.output / f'{args.prefix}-cw-{angle}.pdf'
        writer.write(target)
        target.with_suffix('.expected.json').write_text(json.dumps({
            'saved_rotations': [expected],
            'source_sha256': hashlib.sha256(args.source.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
