"""Generate reproducible synthetic inputs from the actual print routes."""
import argparse
import io
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageEnhance, ImageFilter
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

from .fixtures import fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as root, sync_playwright() as pw:
        app, client, _ = fixture(root)
        browser = pw.chromium.launch()
        page = browser.new_page()
        script = Path('cash/static/js/print.js').read_text().replace('window.addEventListener("load",()=>window.print());', '')
        # Avoid the print dialog independently of the auto-print script's formatting.
        for name, route in [('voucher', '/voucher/FY27-0002/print'),
                            ('addendum', '/voucher/FY27-0002/addendum/FY27-0002-A1/print')]:
            html = client.get(route).text
            start = html.index('  <script src="/static/js/print.js')
            end = html.index('</script>', start) + len('</script>')
            html = html[:start] + '<script>window.print=()=>{};</script><script>' + script + '</script>' + html[end:]
            (args.output / f'{name}.html').write_text(html)
            page.set_content(html)
            pdf = page.pdf(prefer_css_page_size=True, print_background=True)
            (args.output / f'{name}.pdf').write_bytes(pdf)
            assert len(PdfReader(io.BytesIO(pdf)).pages) == 1, name
        browser.close()
    subprocess.run(['pdftoppm', '-scale-to', '2200', '-png', '-singlefile',
                    str(args.output / 'voucher.pdf'), str(args.output / 'voucher')], check=True)
    original = Image.open(args.output / 'voucher.png').convert('RGB')
    original.save(args.output / 'scan.jpg', quality=90)
    original.rotate(12, expand=True, fillcolor='white').save(args.output / 'rotated.jpg', quality=80)
    w, h = original.size
    skewed = original.transform((w, h), Image.Transform.QUAD,
                               (0, 0, -w*.12, h, w*1.1, h*.95, w, -h*.06),
                               Image.Resampling.BICUBIC, fillcolor='white')
    ImageEnhance.Brightness(skewed.filter(ImageFilter.GaussianBlur(.7))).enhance(.65).save(
        args.output / 'perspective-dim.jpg', quality=60)
    original.resize((3024, 4032)).save(args.output / 'phone-12mp.jpg', quality=90)
    Image.new('RGB', (3024, 4032), 'white').save(args.output / 'no-qr-12mp.jpg', quality=90)
    for name, sources in [('same-id-3-pages', ['voucher']*3),
                          ('same-id-40-pages', ['voucher']*40),
                          ('conflicting-ids', ['voucher', 'addendum'])]:
        writer = PdfWriter()
        for source in sources:
            writer.append(args.output / f'{source}.pdf')
        with (args.output / f'{name}.pdf').open('wb') as output:
            writer.write(output)
    print(args.output)


if __name__ == '__main__':
    main()
