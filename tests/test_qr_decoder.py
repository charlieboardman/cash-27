"""Exercise the real decoder on synthetic pages, never private voucher images."""
import io

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
import pytest
import qrcode

from cash.qr_worker import decode_pdf


def photo(codes):
    image = Image.new('RGB', (1800, 2400), '#77736b')
    draw = ImageDraw.Draw(image)
    draw.rectangle((180, 160, 1610, 2230), fill='#efede5')
    for y in range(550, 2100, 70):
        draw.line((300, y, 1400, y), fill='#888888', width=2)
    for value, rotation, x in codes:
        qr = qrcode.make(value, box_size=4, border=4).convert('RGB')
        qr = qr.rotate(rotation, expand=True, fillcolor='white')
        image.paste(qr, (x, 300))
    # Exercise the JPEG path used for camera uploads.
    output = io.BytesIO()
    image.save(output, 'JPEG', quality=88)
    return Image.open(io.BytesIO(output.getvalue())).convert('RGB')


def pdf(tmp_path, pages):
    path = tmp_path / 'input.pdf'
    pages[0].save(path, 'PDF', save_all=True, append_images=pages[1:], resolution=150)
    return path


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_small_qr_in_large_photo_and_clockwise_correction(tmp_path, rotation):
    image = photo([('FY27-0002', 0, 1250)]).rotate(rotation, expand=True)
    assert decode_pdf(pdf(tmp_path, [image])) == {
        'ids': ['FY27-0002'], 'page_ids': [['FY27-0002']],
        'rotations': [rotation], 'qr_pages': [True]}


@pytest.mark.parametrize('codes,ids,correction', [
    ([('FY27-0002', 0, 400), ('FY27-0002', 0, 1200)], ['FY27-0002'], 0),
    ([('FY27-0002', 0, 400), ('FY27-0002', 180, 1200)], ['FY27-0002'], None),
    ([('FY27-0002', 0, 400), ('FY27-0003', 0, 1200)], ['FY27-0002', 'FY27-0003'], None),
    ([('https://example.com/FY27-0002', 0, 400)], [], None),
    ([], [], None),
    ([('FY27-0002', 45, 400)], ['FY27-0002'], None),
    ([('FY27-0002', 2, 400)], ['FY27-0002'], 0),
])
def test_all_codes_and_uncertain_orientation(tmp_path, codes, ids, correction):
    result = decode_pdf(pdf(tmp_path, [photo(codes)]))
    assert result == {'ids': ids, 'page_ids': [ids], 'rotations': [correction],
                      'qr_pages': [bool(ids)]}


def test_page_ids_remain_separate_for_grouping(tmp_path):
    pages = [photo([(code, 0, 1200)]) for code in ['FY27-0002', 'FY27-0002-A1', 'Txn-84']]
    pages.append(photo([]))
    result = decode_pdf(pdf(tmp_path, pages))
    assert result['page_ids'] == [['FY27-0002'], ['FY27-0002-A1'], ['Txn-84'], []]
    assert result['rotations'] == [0, 0, 0, None]
    assert result['qr_pages'] == [True, True, True, False]


def test_existing_pdf_rotation_is_seen_by_decoder(tmp_path):
    path = pdf(tmp_path, [photo([('FY27-0002', 0, 1200)])])
    writer = PdfWriter()
    writer.add_page(PdfReader(path).pages[0].rotate(90))
    with path.open('wb') as output:
        writer.write(output)
    assert decode_pdf(path)['rotations'] == [270]


def test_page_limit(tmp_path):
    writer = PdfWriter()
    for _ in range(41):
        writer.add_blank_page(72, 72)
    path = tmp_path / 'too-many.pdf'
    writer.write(path)
    with pytest.raises(ValueError, match='PDF page limit'):
        decode_pdf(path)


@pytest.mark.parametrize('width,height,expected_size', [
    (72, 144, (200, 400)), (3600, 7200, (1300, 2600)),
])
def test_render_budget_remains_bounded(tmp_path, monkeypatch, width, height, expected_size):
    import zxingcpp
    writer = PdfWriter()
    writer.add_blank_page(width, height)
    path = tmp_path / 'page.pdf'
    writer.write(path)
    observed = []
    read_barcodes = zxingcpp.read_barcodes

    def inspect(image, **options):
        observed.append(image.size)
        return read_barcodes(image, **options)

    monkeypatch.setattr(zxingcpp, 'read_barcodes', inspect)
    assert decode_pdf(path)['ids'] == []
    assert observed == [expected_size]
