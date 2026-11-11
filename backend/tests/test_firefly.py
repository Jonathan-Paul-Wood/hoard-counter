"""Firefly's exported signs, currencies, split journals and import lifecycle."""
import csv
import io
from decimal import Decimal

import pytest

from whc import create_app
from whc.firefly import parse_firefly, stable_id
from whc.models import db, Posting


@pytest.fixture
def household(tmp_path):
    app = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATABASE_URL': 'sqlite:///:memory:',
                      'DATA_DIR': str(tmp_path), 'UPLOAD_DIR': str(tmp_path / 'uploads'),
                      'BACKUP_DIR': str(tmp_path / 'backups')})
    client = app.test_client()
    setup = client.post('/api/v1/auth/setup', json={'username': 'owner', 'password': 'correct horse battery staple'})
    headers = {'X-CSRF-Token': setup.json['csrf_token']}
    def create(kind, data):
        response = client.post('/api/v1/' + kind, headers=headers, json=data)
        assert response.status_code == 201, response.json
        return response.json
    checking = create('accounts', dict(name='Checking', type='Checking', currency='USD', opening_balance='100', opening_date='2024-01-01'))
    savings = create('accounts', dict(name='Savings', type='Savings', currency='USD', opening_balance='0', opening_date='2024-01-01'))
    yield app, client, headers, create, checking, savings
    with app.app_context():
        db.session.remove()
        db.drop_all()


def row(**extra):
    return {**dict(user_id='1', group_id='10', journal_id='20', updated_at='2026-01-05T12:30:00+01:00',
                type='Withdrawal', amount='-10.00', currency_code='USD', foreign_amount='', foreign_currency_code='',
                description='Groceries', date='2026-01-05T00:00:00+14:00',
                source_name='Checking', source_type='Asset account', destination_name='Shop', destination_type='Expense account',
                category='Food', tags='weekly,home', notes='Line one\nLine two'), **extra}


def csv_file(rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode('utf-8-sig')


def preview(client, headers, rows):
    return client.post('/api/v1/imports/preview', headers=headers,
                       data={'kind': 'firefly', 'file': (io.BytesIO(csv_file(rows)), 'firefly.csv')})


def commit(client, headers, response):
    assert response.status_code == 200, response.json
    assert response.json['errors'] == [], response.json
    assert response.json['conflicts'] == [], response.json
    result = client.post('/api/v1/imports/commit', headers=headers, json={'token': response.json['token']})
    assert result.status_code == 200, result.json
    return result


def test_native_export_signs_split_journals_metadata_and_repeat_import(household):
    app, client, headers, create, checking, savings = household
    withdrawal = row()
    withdrawal['amount'] = "'-10.00"  # Firefly's spreadsheet formula escaping.
    rows = [withdrawal,
            row(journal_id='21', type='Deposit', amount='50', source_name='Employer', source_type='Revenue account',
                destination_name='Checking', destination_type='Asset account'),
            row(journal_id='22', type='Transfer', amount='20', destination_name='Savings', destination_type='Asset account')]
    # Each split journal is independent even when group_id is shared.
    response = preview(client, headers, rows)
    assert client.get('/api/v1/accounts/' + checking['id']).json['balance'] == '100'
    assert client.get('/api/v1/transactions').json['total'] == 0
    assert client.get('/api/v1/categories').json['total'] == 0
    assert response.json['summary']['create'] == 8  # 3 transactions, 2 counterparties, category, 2 tags.
    commit(client, headers, response)
    assert Decimal(client.get('/api/v1/accounts/' + checking['id']).json['balance']) == 120
    assert Decimal(client.get('/api/v1/accounts/' + savings['id']).json['balance']) == 20
    transactions = client.get('/api/v1/transactions').json['items']
    assert {item['type'] for item in transactions} == {'Expense', 'Income', 'Transfer'}
    expense = next(item for item in transactions if item['type'] == 'Expense')
    assert Decimal(expense['amount']) == 10
    assert expense['date'] == '2026-01-05'
    assert expense['notes'] == 'Line one\nLine two'
    assert len(expense['tag_ids']) == 2 and expense['category_id']
    assert expense['firefly']['original']['group_id'] == '10'
    assert expense['last_modified'] == '2026-01-05T11:30:00.000000Z'
    repeated = preview(client, headers, rows)
    assert repeated.json['summary']['skip'] == 3, repeated.json
    commit(client, headers, repeated)
    assert client.get('/api/v1/transactions').json['total'] == 3
    with app.app_context():
        for transaction in transactions:
            postings = db.session.scalars(db.select(Posting).where(Posting.transaction_id == transaction['id'])).all()
            assert sum((Decimal(item.usd_amount) for item in postings), Decimal(0)) == 0


def test_newer_export_updates_and_equal_timestamp_conflicts(household):
    app, client, headers, create, checking, savings = household
    commit(client, headers, preview(client, headers, [row()]))
    changed = row(amount='-12')
    response = preview(client, headers, [changed])
    assert response.json['conflicts']
    assert client.post('/api/v1/imports/commit', headers=headers, json={'token': response.json['token']}).status_code == 409
    changed['updated_at'] = '2026-01-06T00:00:00Z'
    commit(client, headers, preview(client, headers, [changed]))
    assert client.get('/api/v1/accounts/' + checking['id']).json['balance'] == '88'
    assert client.get('/api/v1/transactions').json['total'] == 1


def test_foreign_transfer_preserves_both_native_amounts(household):
    app, client, headers, create, checking, savings = household
    create('rates', dict(currency='EUR', date='2024-01-01', rate='1.2'))
    euro = create('accounts', dict(name='Euro savings', type='Savings', currency='EUR', opening_balance='0', opening_date='2024-01-01'))
    rows = [row(type='Transfer', amount='12', foreign_amount='10', foreign_currency_code='EUR',
                destination_name='Euro savings', destination_type='Asset account')]
    commit(client, headers, preview(client, headers, rows))
    assert client.get('/api/v1/accounts/' + euro['id']).json['balance'] == '10'
    assert client.get('/api/v1/accounts/' + checking['id']).json['balance'] == '88'
    transaction = client.get('/api/v1/transactions').json['items'][0]
    assert transaction['amount'] == '12' and transaction['destination_amount'] == '10'


def test_deposit_uses_destination_currency_and_expense_source_is_refund(household):
    app, client, headers, create, checking, savings = household
    create('rates', dict(currency='EUR', date='2024-01-01', rate='1.2'))
    rows = [row(type='Deposit', amount='12', foreign_amount='10', foreign_currency_code='EUR',
                source_name='Shop', source_type='Expense account', destination_name='Checking', destination_type='Asset account')]
    commit(client, headers, preview(client, headers, rows))
    transaction = client.get('/api/v1/transactions').json['items'][0]
    assert transaction['type'] == 'Expense' and transaction['is_refund'] is True
    assert transaction['amount'] == '10' and transaction['destination_amount'] == '12'
    assert client.get('/api/v1/accounts/' + checking['id']).json['balance'] == '112'


@pytest.mark.parametrize('changes,message', [
    ({'source_name': 'Missing'}, 'Create a financial account'),
    ({'currency_code': 'EUR'}, 'in EUR'),
    ({'source_type': 'Unsupported'}, 'Unsupported Firefly account type'),
    ({'type': 'Opening balance'}, 'opening balances'),
    ({'type': 'Reconciliation'}, 'reconciliations'),
    ({'foreign_amount': '5'}, 'provided together'),
    ({'amount': '0'}, 'greater than zero'),
    ({'amount': '-0.001'}, 'decimal places'),
    ({'date': 'not-a-date'}, 'ISO 8601'),
    ({'updated_at': '2026-01-01'}, 'timezone'),
    ({'journal_id': ''}, 'required'),
])
def test_bad_rows_are_actionable_and_never_partially_imported(household, changes, message):
    app, client, headers, create, checking, savings = household
    bad = {**row(journal_id='21'), **changes}
    response = preview(client, headers, [row(), bad])
    errors = str(response.json)
    assert message in errors, response.json
    assert 'row 3' in errors, response.json
    assert client.get('/api/v1/transactions').json['total'] == 0
    assert client.get('/api/v1/categories').json['total'] == 0
    assert client.get('/api/v1/accounts').json['total'] == 8  # two financial + six system counterparties
    if response.status_code == 200:
        assert response.json['errors']
        assert client.post('/api/v1/imports/commit', headers=headers, json={'token': response.json['token']}).status_code == 409
    else:
        assert response.status_code == 400


def test_duplicates_ambiguous_accounts_and_required_headers(household):
    app, client, headers, create, checking, savings = household
    response = preview(client, headers, [row(), row()])
    assert response.status_code == 400 and 'more than once' in str(response.json)
    create('accounts', dict(name='Checking', type='Checking', currency='USD'))
    response = preview(client, headers, [row()])
    assert response.status_code == 400 and 'duplicate names' in str(response.json)
    with pytest.raises(ValueError, match='Missing columns'):
        parse_firefly(b'date,amount\n2026-01-01,5\n')
    with pytest.raises(ValueError, match='UTF-8'):
        parse_firefly(b'\xff\xfe')
    assert stable_id('transaction', '1', '20') != stable_id('transaction', '2', '20')
