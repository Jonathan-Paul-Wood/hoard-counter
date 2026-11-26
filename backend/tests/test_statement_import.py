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
from whc.models import db, Record, AuditEvent, Posting
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
            return dict(transaction_extraction_configured=True, ocr_available=True, limits={})
        assert path == '/v1/transactions'
        assert b'context' in body and b'Groceries and dining' in body
        assert b'output_format' in body and b'account_number' in body and b'description' in body and b'rules' in body
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


def test_csv_systemone_contract_and_full_context(configured, monkeypatch):
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request
    app, client, headers, _, _, (account, _, category) = configured
    with app.app_context():
        record = db.session.get(Record, account)
        record.data = {**record.data, 'description': 'Daily household purchases'}
        save_record('rule', dict(name='Market food', enabled=True, priority=1,
            conditions=[dict(field='description', operator='contains', value='Market')], actions=dict(category_id=category)), None)
        db.session.commit()

    def call(settings, path, body=None, content_type=None):
        assert not db.session().in_transaction()
        if path == '/v1/capabilities':
            return dict(transaction_extraction_configured=True, extraction_configured=False, limits={})
        assert path == '/v1/transactions'
        sent = Request(EnvironBuilder(method='POST', data=body, content_type=content_type).get_environ())
        assert set(sent.form) == {'context', 'output_format'}
        context = json.loads(sent.form['context'])
        own = next(value for value in context['accounts'] if value['id'] == account)
        assert own['description'] == 'Daily household purchases'
        assert own['type'] == 'Checking' and own['account_number'] == '1234567890'
        assert context['rules'][0]['actions']['category_id'] == category
        assert json.loads(sent.form['output_format']) == importer.extraction_schema()
        assert sent.files['documents'].read().startswith(b'Date,Description,Amount')
        return dict(data=dict(transactions=[extracted(account)]), pages=[dict(document=1, page=1)], warnings=[])

    monkeypatch.setattr(importer, 'processor_call', call)
    result = preview(client, headers, [(io.BytesIO(b'Date,Description,Amount\n2025-02-01,Market,-12.50\n'), 'transactions.csv')])
    assert result.status_code == 200, result.json
    assert result.json['rows'][0]['sources'][0]['filename'] == 'transactions.csv'


@pytest.mark.parametrize('payload', [b'junk', b'Date,Amount\n2026-11-15,12,34\n', b'Date,Amount\n2026-11-15,\x00\n'])
def test_invalid_csv_upload_rejected(configured, monkeypatch, payload):
    _, client, headers, *_ = configured
    stub(monkeypatch, [])
    assert preview(client, headers, [(io.BytesIO(payload), 'transactions.csv')]).status_code == 415


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
    monkeypatch.setattr(importer, 'processor_call', lambda *args: dict(transaction_extraction_configured=False))
    assert client.post('/api/v1/ocr/test', headers=headers).status_code == 503
    assert preview(client, headers).status_code == 503
    monkeypatch.setattr(importer, 'processor_call', lambda *args: dict(transaction_extraction_configured=True, limits={'files': 1, 'upload_bytes': 100}))
    assert preview(client, headers).status_code == 413  # Multipart and instructions count too.
    with app.app_context():
        for i in range(400):
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


def match_saved_row(row, candidate):
    pending, saved = row['transaction'], candidate['transaction']
    saved_outgoing = saved['type'] == 'Expense' and not saved.get('is_refund')
    outgoing, incoming = (saved, pending) if saved_outgoing else (pending, saved)
    row.update(existing_transaction_id=candidate['id'], existing_revision=candidate['revision'])
    row['transaction'] = {**saved, 'type': 'Transfer', 'date': outgoing['date'],
        'source_account_id': outgoing['source_account_id'], 'destination_account_id': incoming['destination_account_id'],
        'amount': outgoing['amount'], 'currency': outgoing['currency'],
        'destination_amount': incoming['amount'], 'destination_currency': incoming['currency'],
        'is_refund': False, 'investment_fee': False, 'category_id': None, 'subscription_id': None, 'subscription_date': None}
    row['existing_transfer_candidates'] = []
    return row


@pytest.mark.parametrize('saved_incoming', [False, True])
def test_matches_saved_transactions_and_updates_without_double_counting(configured, monkeypatch, saved_incoming):
    app, client, headers, _, _, (account, other, _) = configured
    original_source = dict(filename='previous.pdf', document=1, page=3)
    with app.app_context():
        saved = save_record('transaction', dict(type='Income' if saved_incoming else 'Expense',
            source_account_id=system_id('income') if saved_incoming else account,
            destination_account_id=other if saved_incoming else system_id('expense'), amount='12.50',
            currency='USD', date='2025-02-02', description='Saved bank entry', notes='Preserve notes',
            merchant_reference='keep', import_sources=[original_source]), None)
        db.session.add(Record(kind='attachment', data={'transaction_id': saved.id, 'filename': 'keep.pdf'}))
        db.session.commit()
        saved_id, saved_revision = saved.id, saved.revision
    stub(monkeypatch, [extracted(account if saved_incoming else other, 'outgoing' if saved_incoming else 'incoming')])
    result = preview(client, headers).json
    row = result['rows'][0]
    candidate = row['existing_transfer_candidates'][0]
    assert candidate['id'] == saved_id and candidate['revision'] == saved_revision
    assert row['transaction']['type'] != 'Transfer'  # Suggestions do not mutate either side.
    with app.app_context():
        assert db.session.get(Record, saved_id).data['type'] != 'Transfer'
    match_saved_row(row, candidate)
    response = approve(client, headers, result)
    assert response.status_code == 200, response.json
    assert response.json['created_count'] == 0 and response.json['updated_count'] == 1
    assert response.json['transaction_ids'] == [saved_id]
    assert approve(client, headers, result).json == response.json
    with app.app_context():
        records = [r for r in list_records('transaction') if not r.data.get('system')]
        assert len(records) == 1
        saved = db.session.get(Record, saved_id)
        assert saved.revision == saved_revision + 1 and saved.data['type'] == 'Transfer'
        assert saved.data['source_account_id'] == account and saved.data['destination_account_id'] == other
        assert saved.data['notes'] == 'Preserve notes' and saved.data['merchant_reference'] == 'keep'
        assert original_source in saved.data['import_sources'] and row['sources'][0] in saved.data['import_sources']
        assert list_records('attachment')[0].data['transaction_id'] == saved_id
        postings = list(db.session.scalars(db.select(Posting).where(Posting.transaction_id == saved_id)))
        assert {posting.account_id for posting in postings} == {account, other}
        assert sum(importer.decimal(posting.usd_amount) for posting in postings) == 0
        audit = list(db.session.scalars(db.select(AuditEvent).where(AuditEvent.record_id == saved_id, AuditEvent.origin == 'statement-import')))
        assert len(audit) == 1 and audit[0].before['type'] != 'Transfer' and audit[0].after['type'] == 'Transfer'


def test_saved_transfer_candidates_obey_amount_currency_date_and_direction(configured):
    app, _, _, _, _, (account, other, _) = configured
    with app.app_context():
        saved = save_record('transaction', dict(type='Expense', source_account_id=account,
            destination_account_id=system_id('expense'), amount='12.50', currency='USD', date='2025-02-01', description='Saved'), None)
        db.session.commit()
        accounts = {record.id: record.to_dict() for record in list_records('account')}
        incoming = dict(type='Income', source_account_id=system_id('income'), destination_account_id=other,
            amount='12.5', currency='USD', date='2025-03-03', description='Incoming')  # Exactly 30 days.
        rows = importer.annotate([dict(id='pending', transaction=incoming)], accounts)
        assert rows[0]['existing_transfer_candidates'][0]['id'] == saved.id
        for changes in ({'date': '2025-03-04'}, {'amount': '12.51'}, {'currency': 'EUR'},
                        {'destination_account_id': account}, {'type': 'Transfer'}):
            assert not importer.annotate([dict(id='pending', transaction={**incoming, **changes})], accounts)[0]['existing_transfer_candidates']
        outgoing = {**incoming, 'type': 'Expense', 'source_account_id': other, 'destination_account_id': system_id('expense')}
        assert not importer.annotate([dict(id='pending', transaction=outgoing)], accounts)[0]['existing_transfer_candidates']
        pending_pair = [dict(id='outgoing', transaction=saved.data), dict(id='incoming', transaction=incoming)]
        assert importer.annotate(pending_pair, accounts)[0]['transfer_candidates'] == ['incoming']
        pending_pair[1]['transaction'] = {**incoming, 'date': '2025-03-04'}
        assert not importer.annotate(pending_pair, accounts)[0]['transfer_candidates']
        transfer = {**incoming, 'type': 'Transfer', 'source_account_id': account}
        importer.validate_existing_transfer(transfer, saved, accounts)
        with pytest.raises(ApiError, match='no longer matches'):
            importer.validate_existing_transfer({**transfer, 'date': '2025-03-04'}, saved, accounts)
        saved.data = {**saved.data, 'type': 'Transfer', 'destination_account_id': other}
        db.session.commit()
        completed = importer.annotate([dict(id='pending', transaction=incoming)], accounts)[0]
        assert not completed['existing_transfer_candidates']
        assert completed['duplicate_ids'] == [saved.id]
        outside_window = importer.annotate([dict(id='pending', transaction={**incoming, 'date': '2025-03-04'})], accounts)[0]
        assert not outside_window['duplicate_ids']


def test_saved_match_rejects_stale_revisions_double_use_and_changed_legs(configured, monkeypatch):
    app, client, headers, _, _, (account, other, _) = configured
    with app.app_context():
        saved = save_record('transaction', dict(type='Expense', source_account_id=account,
            destination_account_id=system_id('expense'), amount='12.50', currency='USD', date='2025-02-01', description='Saved'), None)
        db.session.commit()
        saved_id = saved.id
    stub(monkeypatch, [extracted(other, 'incoming')])
    result = preview(client, headers).json
    row = result['rows'][0]
    match_saved_row(row, row['existing_transfer_candidates'][0])
    original_amount = row['transaction']['amount']
    row['transaction']['amount'] = row['transaction']['destination_amount'] = '14'
    assert approve(client, headers, result).status_code == 409
    row['transaction']['amount'] = row['transaction']['destination_amount'] = original_amount
    duplicate = {**row, 'id': str(uuid4())}
    result['rows'].append(duplicate)
    assert approve(client, headers, result).status_code == 409
    result['rows'].pop()
    with app.app_context():
        saved = db.session.get(Record, saved_id)
        save_record('transaction', {'description': 'Concurrent edit'}, None, saved_id, saved.revision)
        db.session.commit()
    response = approve(client, headers, result)
    assert response.status_code == 409 and response.json['error']['code'] == 'revision_conflict'
    with app.app_context():
        assert db.session.get(Record, saved_id).data['type'] == 'Expense'
        assert db.session.get(Record, result['batch_id']) is None


def test_saved_match_and_new_creation_are_atomic(configured, monkeypatch):
    app, client, headers, _, _, (account, other, _) = configured
    with app.app_context():
        saved = save_record('transaction', dict(type='Expense', source_account_id=account,
            destination_account_id=system_id('expense'), amount='12.50', currency='USD', date='2025-02-01', description='Saved'), None)
        db.session.commit()
        saved_id = saved.id
    stub(monkeypatch, [extracted(other, 'incoming'), extracted(account, amount='20')])
    result = preview(client, headers).json
    match_saved_row(result['rows'][0], result['rows'][0]['existing_transfer_candidates'][0])
    real_save = importer.save_record
    def fail_create(*args, **kwargs):
        if not kwargs.get('record_id'):
            raise ApiError('Simulated save failure', 409)
        return real_save(*args, **kwargs)
    monkeypatch.setattr(importer, 'save_record', fail_create)
    assert approve(client, headers, result).status_code == 409
    with app.app_context():
        assert db.session.get(Record, saved_id).data['type'] == 'Expense'
        assert len([r for r in list_records('transaction') if not r.data.get('system')]) == 1
        assert db.session.get(Record, result['batch_id']) is None


def test_saved_income_matches_its_bank_currency_and_amount(configured, monkeypatch):
    app, client, headers, *_ = configured
    with app.app_context():
        save_record('rate', dict(currency='EUR', date='2025-01-01', rate='1.25'), None)
        sender = save_record('account', dict(name='Euro checking', type='Checking', currency='EUR',
            opening_balance='0', opening_date='2025-01-01'), None)
        receiver = save_record('account', dict(name='Euro savings', type='Savings', currency='EUR',
            opening_balance='0', opening_date='2025-01-01'), None)
        saved = save_record('transaction', dict(type='Income', source_account_id=system_id('income'),
            destination_account_id=receiver.id, amount='12.50', currency='USD', destination_amount='10',
            destination_currency='EUR', date='2025-02-01', description='Converted income'), None)
        db.session.commit()
        sender_id, receiver_id, saved_id = sender.id, receiver.id, saved.id
    stub(monkeypatch, [extracted(sender_id, amount='10', currency='EUR')])
    result = preview(client, headers).json
    row = result['rows'][0]
    candidate = row['existing_transfer_candidates'][0]
    assert candidate['transaction']['amount'] == '10' and candidate['transaction']['currency'] == 'EUR'
    match_saved_row(row, candidate)
    response = approve(client, headers, result)
    assert response.status_code == 200, response.json
    with app.app_context():
        saved = db.session.get(Record, saved_id)
        assert saved.data['currency'] == saved.data['destination_currency'] == 'EUR'
        assert saved.data['source_account_id'] == sender_id and saved.data['destination_account_id'] == receiver_id
        assert importer.decimal(saved.data['amount']) == importer.decimal(saved.data['destination_amount']) == 10
