"""Dummy application data shared by the local benchmark tools."""
import uuid

from cash.web.app import create_app


def fixture(root):
    app = create_app(root, admin_password="benchmark-only", testing=True)
    client = app.test_client()
    token = client.post('/api/login', json={'password': 'benchmark-only'}).json['data']['csrf']
    headers = {'X-CSRF': token}

    def post(url, **data):
        response = client.post(url, json={'submission_id': str(uuid.uuid4()), **data}, headers=headers)
        assert response.status_code == 200, response.json
        return response.json['data']

    post('/api/entries/opening', date='2026-09-01', usd='2000', mxn='15000', assigned_rate_milli=15000)
    for recipient in ['Other Example', 'Alex Example']:
        post('/api/vouchers/prepare', type='signout', date='2026-09-14', recipient=recipient,
             purpose='Purchase classroom supplies for the September program.',
             notes='Sample voucher. Return unused cash and submit receipts after purchase.',
             usd='100', mxn='1500')
    post('/api/addenda/prepare', voucher_id='FY27-0002', kind='positive', date='2026-09-14',
         reason='Additional classroom supplies.', usd='20', mxn='0')
    return app, client, headers
