"""Archive trust boundaries and financial round-trip regressions."""
import io
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from whc import create_app
from whc.ledger import system_id
from whc.models import db, Record, Posting


@pytest.fixture
def household_factory(tmp_path):
    applications = []

    def make():
        root = tmp_path / str(len(applications))
        app = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATA_DIR': str(root),
                          'UPLOAD_DIR': str(root / 'uploads'), 'BACKUP_DIR': str(root / 'backups'),
                          'DATABASE_URL': 'sqlite:///' + str(root / 'test.db')})
        applications.append(app)
        client = app.test_client()
        response = client.post('/api/v1/auth/setup', json={'username': 'owner', 'password': 'a correct long household password'})
        assert response.status_code in (200, 201), response.json
        headers = {'X-CSRF-Token': client.get('/api/v1/auth/session').json['csrf_token']}

        def create(resource, data):
            response = client.post('/api/v1/' + resource, json=data, headers=headers)
            assert response.status_code == 201, response.json
            return response.json

        return app, client, headers, create

    yield make
    for app in applications:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def archive_payload(records, files=None, audits=None):
    import hashlib
    files = files or {}
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('records.json', json.dumps(records))
        archive.writestr('audit.json', json.dumps(audits or []))
        archive.writestr('manifest.json', json.dumps({'format': 'hoard-counter', 'version': 1,
                                                     'files': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}))
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def mutate_archive(payload, mutate):
    with ZipFile(io.BytesIO(payload)) as archive:
        records = json.loads(archive.read('records.json'))
        audits = json.loads(archive.read('audit.json'))
        files = {name: archive.read(name) for name in archive.namelist() if name.startswith('uploads/')}
    mutate(records, audits, files)
    return archive_payload(records, files, audits)


def preview(client, headers, payload):
    return client.post('/api/v1/imports/preview', headers=headers,
                       data={'kind': 'archive', 'file': (io.BytesIO(payload), 'archive.zip')})


def commit_archive(client, headers, payload):
    response = preview(client, headers, payload)
    assert response.status_code == 200, response.json
    assert not response.json['errors'] and not response.json['conflicts'], response.json
    result = client.post('/api/v1/imports/commit', headers=headers, json={'token': response.json['token']})
    assert result.status_code == 200, result.json
    return result


def create_expense(create, amount='10', **extra):
    account = create('accounts', {'name': 'Checking', 'type': 'Checking', 'opening_balance': '100', 'opening_date': '2024-01-01'})
    transaction = create('transactions', {'type': 'Expense', 'source_account_id': account['id'],
                                          'destination_account_id': system_id('expense'), 'date': '2025-01-02',
                                          'amount': amount, 'description': 'Receipt purchase', **extra})
    return account, transaction


def test_full_archive_preserves_historic_fx_valuation_and_reconciliation(household_factory):
    app, source, headers, create = household_factory()
    rate = create('rates', {'currency': 'EUR', 'date': '2024-01-01', 'rate': '1.1'})
    account = create('accounts', {'name': 'Euro portfolio', 'type': 'Brokerage', 'currency': 'EUR',
                                  'opening_balance': '100', 'opening_date': '2024-01-01'})
    mark = create('valuations', {'account_id': account['id'], 'date': '2025-01-01', 'amount': '150'})
    reconciliation = source.post('/api/v1/accounts/' + account['id'] + '/reconcile', headers=headers,
                                 json={'date': '2025-02-01', 'balance': '160', 'revision': account['revision']})
    assert reconciliation.status_code == 200, reconciliation.json
    assert source.patch('/api/v1/rates/' + rate['id'], headers=headers,
                        json={'rate': '2', 'revision': rate['revision']}).status_code == 200
    exported = source.get('/api/v1/exports?format=archive')
    target_app, target, target_headers, _ = household_factory()
    commit_archive(target, target_headers, exported.data)
    assert target.get('/api/v1/accounts/' + account['id']).json['balance'] == '160'
    imported_mark = target.get('/api/v1/valuations/' + mark['id']).json
    assert imported_mark['fx_rate'] == mark['fx_rate'] == '1.1'
    assert imported_mark['usd_amount'] == mark['usd_amount']
    assert Decimal(imported_mark['usd_amount']) == Decimal('165')
    with target_app.app_context():
        original_reconciliation = db.session.get(Record, reconciliation.json['adjustment']['id'])
        assert original_reconciliation.data['fx_rate'] == '1.1'
        for journal in db.session.scalars(db.select(Record).where(Record.kind == 'transaction', Record.deleted.is_(False))):
            postings = db.session.scalars(db.select(Posting).where(Posting.transaction_id == journal.id)).all()
            assert len(postings) >= 2
            assert sum((Decimal(posting.usd_amount) for posting in postings), Decimal(0)) == 0
    # Importing the same archive again is idempotent for every portable record.
    repeated = commit_archive(target, target_headers, exported.data)
    assert repeated.json['summary']['create'] == repeated.json['summary']['update'] == 0


def test_tombstone_history_and_deleted_attachment_roundtrip(household_factory):
    app, source, headers, create = household_factory()
    category = create('categories', {'name': 'Old category'})
    account, transaction = create_expense(create, category_id=category['id'])
    attached = source.post('/api/v1/attachments', headers=headers, data={'transaction_id': transaction['id'],
                            'file': (io.BytesIO(b'%PDF-1.4\nreceipt'), 'receipt.pdf')})
    assert attached.status_code == 201
    assert source.delete('/api/v1/attachments/' + attached.json['id'], headers=headers,
                         json={'revision': attached.json['revision']}).status_code == 200
    assert source.delete('/api/v1/transactions/' + transaction['id'], headers=headers,
                         json={'revision': transaction['revision']}).status_code == 200
    assert source.delete('/api/v1/categories/' + category['id'], headers=headers,
                         json={'revision': category['revision']}).status_code == 200
    exported = source.get('/api/v1/exports?format=archive')
    destination, client, other_headers, _ = household_factory()
    commit_archive(client, other_headers, exported.data)
    with destination.app_context():
        for identity in (transaction['id'], category['id'], attached.json['id']):
            assert db.session.get(Record, identity).deleted
        assert not db.session.scalar(db.select(Posting.id).where(Posting.transaction_id == transaction['id']))
    assert client.get('/api/v1/accounts/' + account['id']).json['balance'] == '100'
    assert client.get('/api/v1/attachments/' + attached.json['id']).status_code == 404


def test_newer_import_replaces_optional_fields_and_equal_timestamp_tombstone_conflicts(household_factory):
    app, client, headers, create = household_factory()
    _, transaction = create_expense(create, notes='Remove this note')
    exported = client.get('/api/v1/exports?format=archive').data

    def tombstone(records, audits, files):
        next(row['record'] for row in records if row['record']['id'] == transaction['id'])['deleted'] = True

    conflicting = preview(client, headers, mutate_archive(exported, tombstone))
    assert conflicting.status_code == 200
    assert conflicting.json['summary']['conflict'] == 1
    assert client.post('/api/v1/imports/commit', headers=headers, json={'token': conflicting.json['token']}).status_code == 409
    assert client.get('/api/v1/transactions/' + transaction['id']).json['notes'] == 'Remove this note'

    def newer(records, audits, files):
        item = next(row['record'] for row in records if row['record']['id'] == transaction['id'])
        item.pop('notes')
        item['last_modified'] = '2030-01-01T00:00:00Z'

    committed = commit_archive(client, headers, mutate_archive(exported, newer))
    assert committed.json['summary']['update'] == 1
    assert 'notes' not in client.get('/api/v1/transactions/' + transaction['id']).json
    stale = commit_archive(client, headers, exported)
    assert stale.json['summary']['update'] == 0


def test_uuid_alias_duplicate_and_system_account_bypass_are_rejected(household_factory):
    app, client, headers, create = household_factory()
    identity = str(uuid4())
    record = {'id': identity, 'name': 'First', 'last_modified': '2025-01-01T00:00:00Z'}
    duplicate = archive_payload([{'kind': 'tag', 'record': record},
                                 {'kind': 'tag', 'record': {**record, 'id': identity.upper()}}])
    response = preview(client, headers, duplicate)
    assert response.status_code == 400
    assert 'same UUID' in response.json['message']
    malicious = archive_payload([{'kind': 'account', 'record': {**record, 'system': 'fake', 'type': 'anything'}}])
    response = preview(client, headers, malicious)
    assert response.status_code == 200
    assert response.json['errors']
    with app.app_context():
        assert db.session.get(Record, identity) is None


@pytest.mark.parametrize('corruption', ['filename', 'mime', 'size', 'audit'])
def test_archive_attachment_metadata_and_history_verified_before_commit(household_factory, corruption):
    app, client, headers, create = household_factory()
    _, transaction = create_expense(create)
    uploaded = client.post('/api/v1/attachments', headers=headers,
                           data={'transaction_id': transaction['id'], 'file': (io.BytesIO(b'%PDF-1.4\ncontent'), 'document.pdf')})
    assert uploaded.status_code == 201
    original = client.get('/api/v1/exports?format=archive').data

    def corrupt(records, audits, files):
        item = next(row['record'] for row in records if row['kind'] == 'attachment')
        if corruption == 'filename':
            old_path = item['path']
            item['path'] = str(uuid4()) + '.pdf'
            files['uploads/' + item['path']] = files.pop('uploads/' + old_path)
        elif corruption == 'mime':
            item['mime_type'] = 'text/html'
        elif corruption == 'size':
            item['size'] += 1
        else:
            audits.append({'id': str(uuid4()), 'created_at': 'invalid'})

    other_app, other, other_headers, _ = household_factory()
    response = preview(other, other_headers, mutate_archive(original, corrupt))
    assert response.status_code == 400, response.json
    assert not list(Path(other_app.config['UPLOAD_DIR']).iterdir())
    with other_app.app_context():
        assert db.session.get(Record, transaction['id']) is None


def test_download_requires_login_and_path_traversal_archive_is_rejected(household_factory):
    app, client, headers, create = household_factory()
    _, transaction = create_expense(create)
    uploaded = client.post('/api/v1/attachments', headers=headers,
                           data={'transaction_id': transaction['id'], 'file': (io.BytesIO(b'%PDF-1.4\ncontent'), '../receipt.pdf')})
    assert uploaded.status_code == 201
    assert uploaded.json['filename'] == 'receipt.pdf'
    assert app.test_client().get('/api/v1/attachments/' + uploaded.json['id']).status_code == 401
    assert client.get('/api/v1/attachments/' + uploaded.json['id']).headers['X-Content-Type-Options'] == 'nosniff'
    response = preview(client, headers, archive_payload([], {'uploads/../escape.pdf': b'%PDF-1.4\ncontent'}))
    assert response.status_code == 400
    assert not (Path(app.config['DATA_DIR']) / 'escape.pdf').exists()


def test_attachment_collision_aborts_entire_import_before_any_file_publication(household_factory):
    app, source, headers, create = household_factory()
    account, transaction = create_expense(create)
    attachments = []
    for number in range(2):
        response = source.post('/api/v1/attachments', headers=headers,
                               data={'transaction_id': transaction['id'],
                                     'file': (io.BytesIO(b'%PDF-1.4\n' + str(number).encode()), f'receipt{number}.pdf')})
        assert response.status_code == 201
        attachments.append(response.json)
    archive = source.get('/api/v1/exports?format=archive').data
    target_app, client, target_headers, _ = household_factory()
    prepared = preview(client, target_headers, archive)
    assert prepared.status_code == 200
    assert not prepared.json['errors'] and not prepared.json['conflicts']
    target_uploads = Path(target_app.config['UPLOAD_DIR'])
    collision = target_uploads / attachments[1]['path']
    collision.write_bytes(b'existing immutable content')
    committed = client.post('/api/v1/imports/commit', headers=target_headers, json={'token': prepared.json['token']})
    assert committed.status_code == 409
    assert collision.read_bytes() == b'existing immutable content'
    assert not (target_uploads / attachments[0]['path']).exists()
    with target_app.app_context():
        assert db.session.get(Record, account['id']) is None
        assert db.session.get(Record, transaction['id']) is None
