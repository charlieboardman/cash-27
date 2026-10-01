"""Socket-free HTTP transport for the GAS harness, using the real Flask routes."""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cash.web.app import create_app

root, path, encoded_options = sys.argv[1:]
options = json.loads(encoded_options)
app = create_app(root, admin_password='x', testing=True)
with app.test_client() as client:
    response = client.open(path, method=options.get('method', 'get'),
                           headers=options.get('headers', {}),
                           content_type=options.get('contentType'), data=options.get('payload'))
    print(json.dumps({'status': response.status_code, 'headers': dict(response.headers),
                      'body': base64.b64encode(response.data).decode()}))
