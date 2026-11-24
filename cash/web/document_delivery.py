"""Shared verified PDF delivery for admin and voucher-scoped routes."""
import hashlib

from flask import current_app, g, redirect, request, send_file

from .. import document_names
from .app import err
from .documents import document_page


def document_url(document_id, voucher_id=None):
    # Request-local cache only; the next request observes renumbering.
    if not hasattr(g, 'document_filenames'):
        g.document_filenames = document_names.canonical_filenames(g.db)
    return document_names.document_url(document_id, g.document_filenames, voucher_id)


def verified_document(document_id, voucher_id=None):
    sql = 'SELECT rel_path, sha256 FROM documents WHERE document_id=?'
    args = [document_id]
    if voucher_id is not None:
        sql += ' AND voucher_id=?'
        args.append(voucher_id)
    row = g.db.execute(sql, args).fetchone()
    if row is None:
        return None, err('not_found', 'Document not found', 404)
    path = current_app.config['DATA_ROOT'] / row['rel_path']
    if not path.is_file():
        return None, err('missing', 'File missing', 410)
    if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
        return None, err('hash_mismatch', 'File corrupted', 410)
    return (path, row['sha256']), None


def page_image(document_id, page_number, voucher_id=None):
    """Render one page under the same auth rules as the PDF itself."""
    verified, failure = verified_document(document_id, voucher_id)
    if failure is not None:
        return failure
    try:
        width = int(request.args.get("width", "1600"))
        if width not in (1200, 1600, 2400):
            raise ValueError("width")
    except ValueError:
        return err("bad_request", "Invalid preview width", 400)
    try:
        out = document_page(verified[0], page_number, width)
    except IndexError:
        return err("not_found", "Page not found", 404)
    except Exception:
        return err("preview_unavailable", "Preview unavailable", 422)
    response = send_file(out, mimetype="image/png")
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


def serve_document(document_id, requested_filename, voucher_id=None):
    verified, failure = verified_document(document_id, voucher_id)
    if failure is not None:
        return failure
    url = document_url(document_id, voucher_id)
    canonical = document_names.filename(document_id, g.document_filenames)
    if requested_filename != canonical:
        response = redirect(url, code=302)
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    response = send_file(verified[0], mimetype='application/pdf', download_name=canonical)
    response.headers['Cache-Control'] = 'private, no-cache, must-revalidate'
    return response
