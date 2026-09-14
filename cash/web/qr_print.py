"""Small vector QR codes for paper voucher identifiers."""

import io

import qrcode
from qrcode.image.svg import SvgPathImage
from markupsafe import Markup


def qr_svg(identifier):
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                         border=4)
    code.add_data(identifier)
    code.make(fit=True)
    output = io.BytesIO()
    code.make_image(image_factory=SvgPathImage).save(output)
    return Markup(output.getvalue().decode("utf-8"))
