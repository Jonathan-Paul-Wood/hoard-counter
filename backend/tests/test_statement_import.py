import io
import json
import zipfile
from uuid import uuid4
from urllib.error import HTTPError, URLError

import pytest
from sqlalchemy.exc import IntegrityError

from test_admin_auth import household
from whc import statement_import as importer
from whc.ledger import save_record, list_records, system_id
from whc.models import db, Record, AuditEvent
from whc.validation import ApiError


@pytest.fixture
def configured(household):
    app, client, headers, _, member, member_headers, _ = household
    assert client.patch('/api/v1/settings', json={'ocr_url': 'http://processor:8081', 'ocr_token': 'secret-token'}, headers=headers).status_code == 200
    with app.app_context():
        account = save_record('account', dict(name='Checking', type='Checking', account_number='1234567890',
            opening_balance='0', opening_date='2025-01-01'), None)
        other = save_record('account', dict(name='Savings', type='Savings', account_number='5555666677',
            opening_balance='0', opening_date='2025-01-01'), None)
        category = save_record('category', dict(name='Food', description='Groceries and dining'), None)
        db.session.commit()
        ids = account.id, other.id, category.id
    return app, client, headers, member, member_headers, ids


def extracted(account_id=None, direction='outgoing', **changes):
    return dict(date='2025-02-01', description='Store purchase', amount='12.50', currency='USD',
        account_id=account_id, account_number=None, source_account_id=None, destination_account_id=None,
        category_id=None, direction=direction, is_refund=False, document=1, page=1, review_notes=None) | changes


def stub(monkeypatch, items):
    def call(settings, path, body=None, content_type=None):
        assert not db.session().in_transaction(), 'Network must not hold a database transaction'
        if path == '/v1/capabilities':
            return dict(extraction_configured=True, ocr_available=True, limits={})
        assert b'Known accounts:' in body and b'Groceries and dining' in body
        assert content_type.startswith('multipart/form-data; boundary=')
        return dict(data=dict(transactions=items), warnings=['Review extracted values.'],
                    pages=[{'document': i, 'page': 1, 'text': 'private text'} for i in (1, 2)])
    monkeypatch.setattr(importer, 'processor_call', call)


def preview(client, headers, documents=None):
    return client.post('/api/v1/transactions/import/preview', headers=headers,
        data={'documents': documents or [(io.BytesIO(b'%PDF-test'), 'statement.pdf')]}, content_type='multipart/form-data')


def approve(client, headers, result):
    return client.post('/api/v1/transactions/import/commit', headers=headers,
        json={'batch_id': result['batch_id'], 'rows': result['rows']})


def test_settings_permissions_redaction_and_disconnect(configured):
    app, client, headers, member, mh, _ = configured
    response = member.get('/api/v1/settings')
    assert response.json['ocr_configured'] and response.json['ocr_token_saved']
    assert 'secret-token' not in response.get_data(as_text=True)
    assert member.patch('/api/v1/settings', json={'ocr_url': ''}, headers=mh).status_code == 403
    assert member.post('/api/v1/ocr/test', headers=mh).status_code == 403
    assert client.patch('/api/v1/settings', json={'household_name': 'Mine'}, headers=headers).json['ocr_token_saved']
    with app.app_context():
        assert 'secret-token' not in json.dumps([event.to_dict() for event in db.session.scalars(db.select(AuditEvent))])
    with zipfile.ZipFile(io.BytesIO(client.get('/api/v1/exports?format=archive').data)) as archive:
        assert b'secret-token' not in archive.read('records.json')
        assert b'secret-token' not in archive.read('audit.json')
    assert client.patch('/api/v1/settings', json={'ocr_token': 'replacement-token'}, headers=headers).json['ocr_token_saved']
    assert client.patch('/api/v1/settings', json={'ocr_token': ''}, headers=headers).json['ocr_token_saved'] is False
    assert client.patch('/api/v1/settings', json={'ocr_url': ''}, headers=headers).json['ocr_configured'] is False
    assert preview(client, headers).status_code == 409


@pytest.mark.parametrize('url', ['ftp://processor', 'http://user:secret@processor', 'http://processor?q=secret', 'http://processor/#secret', 'http://processor:bad', 'http://processor\n'])
def test_invalid_settings_url(configured, url):
    _, client, headers, *_ = configured
    assert client.patch('/api/v1/settings', json={'ocr_url': url}, headers=headers).status_code == 400


def test_preview_and_idempotent_atomic_approval(configured, monkeypatch):
    app, client, headers, member, mh, (account, _, category) = configured
    stub(monkeypatch, [extracted(account, category_id=category)])
    response = preview(member, mh)
    assert response.status_code == 200, response.json
    result = response.json
    row = result['rows'][0]
    assert row['selected'] and not row['errors']
    assert row['transaction']['source_account_id'] == account
    assert row['sources'][0]['filename'] == 'statement.pdf'
    assert 'private text' not in response.get_data(as_text=True)
    with app.app_context():
        assert not [r for r in list_records('transaction') if not r.data.get('system')]
    row['transaction']['description'] = 'Reviewed purchase'
    committed = approve(member, mh, result)
    assert committed.status_code == 200, committed.json
    assert approve(member, mh, result).json == committed.json
    row['transaction']['description'] = 'Changed after commit'
    assert approve(member, mh, result).status_code == 409
    with app.app_context():
        records = [r for r in list_records('transaction') if not r.data.get('system')]
        assert len(records) == 1 and records[0].data['description'] == 'Reviewed purchase'
        assert records[0].data['import_sources'] == row['sources']
        events = list(db.session.scalars(db.select(AuditEvent).where(AuditEvent.origin == 'statement-import')))
        assert events


def test_rules_only_apply_before_user_review(configured, monkeypatch):
    app, client, headers, _, _, (account, _, category) = configured
    with app.app_context():
        save_record('rule', dict(name='Food stores', enabled=True, priority=1,
            conditions=[dict(field='description', operator='contains', value='Store')], actions=dict(category_id=category)), None)
        db.session.commit()
    stub(monkeypatch, [extracted(account)])
    result = preview(client, headers).json
    assert result['rows'][0]['transaction']['category_id'] == category
    result['rows'][0]['transaction']['category_id'] = None
    assert approve(client, headers, result).status_code == 200
    with app.app_context():
        assert [r for r in list_records('transaction') if not r.data.get('system')][0].data['category_id'] is None


def test_transfers_duplicates_and_new_duplicate_acknowledgment(configured, monkeypatch):
    app, client, headers, _, _, (account, other, _) = configured
    incoming = extracted(other, 'incoming')
    incoming['document'] = 2
    stub(monkeypatch, [extracted(account), incoming])
    result = preview(client, headers, [(io.BytesIO(b'%PDF-one'), 'one.pdf'), (io.BytesIO(b'%PDF-two'), 'two.pdf')]).json
    left, right = result['rows']
    assert left['transfer_candidates'] == [right['id']]
    assert left['transaction']['type'] == 'Expense' and right['transaction']['type'] == 'Income'
    with app.app_context():
        save_record('transaction', left['transaction'], None)
        db.session.commit()
    result['rows'] = [left]
    rejected = approve(client, headers, result)
    assert rejected.status_code == 409
    left['acknowledged_duplicates'] = rejected.json['error']['details']['rows'][0]['duplicate_ids']
    assert approve(client, headers, result).status_code == 200
    stub(monkeypatch, [extracted(account), extracted(account)])
    result = preview(client, headers).json
    assert all(not row['selected'] and row['duplicate_ids'] for row in result['rows'])


def test_account_matching_unknowns_refunds_and_validation(configured, monkeypatch):
    _, client, headers, _, _, (account, _, _) = configured
    stub(monkeypatch, [extracted(account_number='****7890'), extracted(None, 'incoming', is_refund=True), extracted('unknown')])
    result = preview(client, headers).json
    assert result['rows'][0]['transaction']['source_account_id'] == account
    assert result['rows'][1]['transaction']['type'] == 'Expense'
    assert result['rows'][1]['transaction']['source_account_id'] == system_id('expense')
    assert result['rows'][2]['errors']
    assert approve(client, headers, result).status_code == 400
    assert importer.match_account('****1234', {'a': {'account_number': '11111234'}, 'b': {'account_number': '22221234'}}) is None


def test_failed_commit_rolls_back_all_rows(configured, monkeypatch):
    app, client, headers, _, _, (account, other, _) = configured
    stub(monkeypatch, [extracted(account), extracted(other)])
    result = preview(client, headers).json
    real_save = importer.save_record
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ApiError('Simulated concurrent failure', 409)
        return real_save(*args, **kwargs)
    monkeypatch.setattr(importer, 'save_record', fail_second)
    assert approve(client, headers, result).status_code == 409
    with app.app_context():
        assert not [r for r in list_records('transaction') if not r.data.get('system')]
        assert db.session.get(Record, result['batch_id']) is None


@pytest.mark.parametrize('items', [[{'amount': 10}], [extracted(document=9)], [extracted(page=0)]])
def test_reject_malformed_processor_response(configured, monkeypatch, items):
    _, client, headers, *_ = configured
    stub(monkeypatch, items)
    assert preview(client, headers).status_code == 502


def test_empty_results_limits_and_csrf(configured, monkeypatch):
    _, client, headers, *_ = configured
    stub(monkeypatch, [])
    assert preview(client, headers).json['rows'] == []
    assert preview(client, {}).status_code == 403
    assert preview(client, headers, [(io.BytesIO(b'not a file'), 'bad.csv')]).status_code == 415
    assert preview(client, headers, [(io.BytesIO(b'%PDF-file'), str(i)) for i in range(6)]).status_code == 400
    assert preview(client, headers, [(io.BytesIO(b'%PDF-' + b' ' * importer.MAX_UPLOAD), 'large.pdf')]).status_code == 413


@pytest.mark.parametrize('exception,status', [(TimeoutError(), 504), (URLError('secret-url'), 503),
    (HTTPError('secret-url', 401, 'secret-token', {}, None), 502),
    (HTTPError('secret-url', 429, 'busy', {}, None), 429)])
def test_adapter_errors_are_sanitized(monkeypatch, exception, status):
    class Opener:
        def open(self, req, timeout):
            assert req.headers['Authorization'] == 'Bearer secret-token'
            raise exception
    monkeypatch.setattr(importer, 'build_opener', lambda handler: Opener())
    with pytest.raises(ApiError) as error:
        importer.processor_call(dict(ocr_url='http://processor', ocr_token='secret-token'), '/v1/extract', b'x')
    assert error.value.status == status
    assert 'secret-token' not in error.value.message and 'secret-url' not in error.value.message
    assert importer.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://other') is None


def test_category_limits_api_and_raw_database(configured):
    app, client, headers, *_ = configured
    assert client.post('/api/v1/categories', json={'name': 'x' * 36, 'description': 'd' * 120}, headers=headers).status_code == 201
    assert client.post('/api/v1/categories', json={'name': 'x' * 37}, headers=headers).status_code == 400
    assert client.post('/api/v1/categories', json={'name': 'fine', 'description': 'd' * 121}, headers=headers).status_code == 400
    assert client.post('/api/v1/tags', json={'name': 'x' * 37}, headers=headers).status_code == 400
    with app.app_context():
        db.session.add(Record(kind='tag', data={'name': 'x' * 37}))
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()


def test_service_limits_context_and_readiness(configured, monkeypatch):
    app, client, headers, *_ = configured
    monkeypatch.setattr(importer, 'processor_call', lambda *args: dict(extraction_configured=False))
    assert client.post('/api/v1/ocr/test', headers=headers).status_code == 503
    assert preview(client, headers).status_code == 503
    monkeypatch.setattr(importer, 'processor_call', lambda *args: dict(extraction_configured=True, limits={'files': 1, 'upload_bytes': 100}))
    assert preview(client, headers).status_code == 413  # Multipart and instructions count too.
    with app.app_context():
        for i in range(110):
            save_record('category', dict(name=f'Category {i}', description='x' * 120), None)
        db.session.commit()
    def unexpected(*args):
        raise AssertionError('Oversized context must be rejected before network calls')
    monkeypatch.setattr(importer, 'processor_call', unexpected)
    assert preview(client, headers).status_code == 413


def test_account_changes_and_missing_rates_revalidated(configured, monkeypatch):
    app, client, headers, _, _, (account, _, _) = configured
    stub(monkeypatch, [extracted(account)])
    result = preview(client, headers).json
    with app.app_context():
        record = db.session.get(Record, account)
        record.data = {**record.data, 'closed_on': '2025-03-01'}
        db.session.commit()
    response = approve(client, headers, result)
    assert response.status_code == 400
    assert 'archived' in response.json['error']['details']['errors'][0]
    with app.app_context():
        record = db.session.get(Record, account)
        record.data = {**record.data, 'closed_on': None}
        db.session.commit()
    result['rows'][0]['transaction']['currency'] = 'EUR'
    response = approve(client, headers, result)
    assert response.status_code == 400
    assert 'rate' in response.json['error']['details']['errors'][0].lower()


def test_category_description_archive_round_trip(configured):
    app, client, headers, *_ = configured
    exported = client.get('/api/v1/exports?format=archive')
    with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
        categories = [entry['record'] for entry in json.loads(archive.read('records.json')) if entry['kind'] == 'category']
    assert categories[0]['description'] == 'Groceries and dining'
    response = client.post('/api/v1/imports/preview', data={'kind': 'archive', 'file': (io.BytesIO(exported.data), 'archive.zip')}, headers=headers)
    assert response.status_code == 200, response.json
    assert not response.json['errors']
    assert not response.json['conflicts']
    assert 'secret-token' not in response.get_data(as_text=True)


def test_rule_subscription_link_is_reviewed_and_can_be_removed(configured, monkeypatch):
    app, client, headers, _, _, (account, _, _) = configured
    with app.app_context():
        subscription = save_record('subscription', dict(name='Monthly store', type='Expense', source_account_id=account,
            destination_account_id=system_id('expense'), amount='12.50', currency='USD', start_date='2025-02-01', frequency='monthly'), None)
        save_record('rule', dict(name='Link monthly store', conditions=[dict(field='description', operator='contains', value='Store')],
            actions=dict(subscription_id=subscription.id)), None)
        db.session.commit()
        subscription_id = subscription.id
    stub(monkeypatch, [extracted(account)])
    result = preview(client, headers).json
    transaction = result['rows'][0]['transaction']
    assert transaction['subscription_id'] == subscription_id and transaction['subscription_date'] == '2025-02-01'
    assert not result['rows'][0]['errors']
    transaction['subscription_id'] = None
    transaction['subscription_date'] = None
    assert approve(client, headers, result).status_code == 200
    with app.app_context():
        records = [r for r in list_records('transaction') if not r.data.get('system')]
        assert not records[0].data.get('subscription_id')
