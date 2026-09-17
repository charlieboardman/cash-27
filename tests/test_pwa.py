"""Public installation metadata for the online-only app."""
import io
import json

from PIL import Image

from cash.web.app import create_app


def test_manifest_and_icons_are_public_and_launch_the_existing_app(tmp_path):
    client = create_app(tmp_path, admin_password='test', testing=True).test_client()
    response = client.get('/static/manifest.webmanifest')
    assert response.status_code == 200
    assert response.mimetype == 'application/manifest+json'
    manifest = json.loads(response.data)
    assert manifest['id'] == manifest['scope'] == manifest['start_url'] == '/'
    assert manifest['display'] == 'standalone'
    assert manifest['name'] == 'Cash'
    for icon in manifest['icons']:
        response = client.get(icon['src'])
        assert response.status_code == 200
        assert response.mimetype == 'image/png'
        image = Image.open(io.BytesIO(response.data))
        assert f'{image.width}x{image.height}' == icon['sizes']
    apple = client.get('/static/icons/cash-180.png')
    assert Image.open(io.BytesIO(apple.data)).size == (180, 180)
    assert b'/static/manifest.webmanifest' in client.get('/login').data
    client.post('/api/login', json={'password': 'test'})
    assert b'/static/manifest.webmanifest' in client.get('/balances').data
    assert client.get('/').location.endswith('/setup')
