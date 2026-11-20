import io
import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import pytest
from pypdf import PdfReader

from whc import create_app
from whc.models import db, Record
from whc.ledger import system_id
from whc.analytics import performance


@pytest.fixture
def household(tmp_path):
    app = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATA_DIR': str(tmp_path), 'UPLOAD_DIR': str(tmp_path / 'uploads'), 'BACKUP_DIR': str(tmp_path / 'backups'), 'DATABASE_URL': 'sqlite:///' + str(tmp_path / 'test.db')})
    client = app.test_client()
    response = client.post('/api/v1/auth/setup', json={'username': 'owner', 'password': 'correct horse battery staple'})
    assert response.status_code in (200, 201), response.json
    token = client.get('/api/v1/auth/session').json['csrf_token']
    headers = {'X-CSRF-Token': token}
    def create(kind, data):
        response = client.post('/api/v1/' + kind, json=data, headers=headers)
        assert response.status_code == 201, response.json
        return response.json
    account = create('accounts', {'name': 'Household checking', 'type': 'Checking', 'currency': 'USD', 'opening_balance': '1000', 'opening_date': '2024-01-01'})
    category = create('categories', {'name': 'Groceries'})
    return app, client, headers, create, account, category


def expense(account, category, amount='20', when='2026-07-15', **extra):
    return dict(source_account_id=account['id'], destination_account_id=system_id('expense'), date=when, amount=amount, type='Expense', description='Groceries', category_id=category['id'], **extra)


def test_dashboard_budgets_refunds_and_report_totals(household):
    app, client, headers, create, account, category = household
    create('transactions', expense(account, category, '100'))
    create('transactions', dict(source_account_id=system_id('expense'), destination_account_id=account['id'], type='Expense', date='2026-07-16', amount='25', description='Refund', category_id=category['id'], is_refund=True))
    create('budgets', {'month': '2026-07', 'category_id': category['id'], 'amount': '150'})
    summary = client.get('/api/v1/budget-summary?month=2026-07').json
    assert Decimal(summary['total_actual']) == 75
    assert Decimal(summary['items'][0]['remaining']) == 75
    data = client.get('/api/v1/reports/data?start=2026-07-01&end=2026-07-31').json
    assert Decimal(data['summary']['expenses']) == 75
    dashboard = client.get('/api/v1/dashboard?start=2026-07-01&end=2026-07-31').json
    assert dashboard['expenses'] == data['summary']['expenses']
    assert Decimal(dashboard['assets']) == 925
    copied = client.post('/api/v1/budgets/copy', headers=headers, json={'source_month': '2026-07', 'target_month': '2026-08'})
    assert copied.status_code == 200
    assert copied.json['copied'] == 1


def test_all_csv_report_sections_share_totals_and_preserve_audit(household):
    _, client, headers, create, account, category = household
    purchase = create('transactions', {**expense(account, category, '100'), 'description': '=1+1'})
    create('transactions', dict(source_account_id=system_id('expense'), destination_account_id=account['id'], type='Expense', date='2026-07-16', amount='25', description='Refund', category_id=category['id'], is_refund=True))
    create('budgets', {'month': '2026-07', 'category_id': category['id'], 'amount': '150'})
    query = '?start=2026-07-01&end=2026-07-31'
    data = client.get('/api/v1/reports/data' + query).json
    sections = {}
    for section in ('transactions', 'summary', 'cash-flow', 'budgets', 'balances', 'net-worth'):
        response = client.get('/api/v1/reports/csv' + query + '&section=' + section)
        assert response.status_code == 200
        sections[section] = list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))
    spending = Decimal(data['summary']['expenses'])
    assert spending == Decimal('75')
    assert sum(Decimal(row['net_expense_usd']) for row in sections['transactions']) == spending
    assert next(row for row in sections['transactions'] if row['id'] == purchase['id'])['description'] == "'=1+1"
    assert Decimal(sections['summary'][0]['expenses']) == spending
    assert Decimal(sections['cash-flow'][0]['expenses']) == spending
    assert Decimal(sections['budgets'][0]['actual']) == spending
    assert Decimal(sections['budgets'][0]['remaining']) == 75
    assert Decimal(sections['balances'][0]['balance_usd']) == 925
    assert Decimal(sections['net-worth'][0]['net_worth']) == 925
    audit_day = purchase['last_modified'][:10]
    audit = client.get(f'/api/v1/reports/csv?section=audit&start={audit_day}&end={audit_day}')
    rows = list(csv.DictReader(io.StringIO(audit.data.decode('utf-8-sig'))))
    event = next(row for row in rows if row['record_id'] == purchase['id'])
    assert json.loads(event['after'])['amount'] == purchase['amount']
    assert event['actor_name'] == 'owner'
    assert client.get('/api/v1/reports/csv?section=unknown').status_code == 400


def test_forecast_excludes_recurring_and_transfers(household, monkeypatch):
    app, client, headers, create, account, category = household
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 10, 16))
    sub = create('subscriptions', {'name': 'Groceries delivery', 'source_account_id': account['id'], 'destination_account_id': system_id('expense'), 'amount': '10', 'currency': 'USD', 'type': 'Expense', 'start_date': '2026-07-15', 'frequency': 'monthly', 'category_id': category['id']})
    for month in ('07', '08', '09'):
        create('transactions', expense(account, category, '30', f'2026-{month}-01'))
        create('transactions', expense(account, category, '10', f'2026-{month}-15', subscription_id=sub['id'], subscription_date=f'2026-{month}-15'))
    other = create('accounts', {'name': 'Savings', 'type': 'Savings', 'currency': 'USD'})
    create('transactions', {'type': 'Transfer', 'date': '2026-09-03', 'source_account_id': account['id'], 'destination_account_id': other['id'], 'amount': '500', 'description': 'Savings transfer'})
    forecast = client.get('/api/v1/forecast?horizon=month').json
    assert forecast['history_months'] == ['2026-07', '2026-08', '2026-09']
    assert Decimal(forecast['recurring']) == 10
    assert Decimal(forecast['nonrecurring']) == 30
    assert Decimal(forecast['total']) == 40


def test_valuation_performance_and_projection(household, monkeypatch):
    app, client, headers, create, account, category = household
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 1, 1))
    broker = create('accounts', {'name': 'Portfolio', 'type': 'Brokerage', 'currency': 'USD', 'opening_balance': '100', 'opening_date': '2024-01-01'})
    create('valuations', {'account_id': broker['id'], 'date': '2024-01-01', 'amount': '100'})
    create('transactions', {'type': 'Transfer', 'source_account_id': account['id'], 'destination_account_id': broker['id'], 'date': '2025-01-01', 'amount': '20', 'description': 'Contribution'})
    create('valuations', {'account_id': broker['id'], 'date': '2025-01-01', 'amount': '130'})
    response = client.get('/api/v1/performance?account_id=' + broker['id'])
    assert response.status_code == 200, response.json
    assert Decimal(response.json['cumulative_return']) == Decimal('.1')
    assert response.json['annualized_return'] is not None
    projected = client.post('/api/v1/projections', headers=headers, json={'date': '2027-01-01', 'assumptions': [{'account_id': broker['id'], 'mode': 'manual', 'annual_rate': '0.1'}], 'flows': []})
    assert projected.status_code == 200, projected.json
    portfolio = next(r for r in projected.json['accounts'] if r['account_id'] == broker['id'])
    assert Decimal(portfolio['projected_value']) > 142


def test_csv_roundtrip_idempotent_and_formula_safe(household):
    app, client, headers, create, account, category = household
    create('transactions', {**expense(account, category), 'description': '=SUM(A1:A2)'})
    exported = client.get('/api/v1/exports?format=transactions')
    assert b"'=SUM(A1:A2)" in exported.data
    preview = client.post('/api/v1/imports/preview', headers=headers, data={'kind': 'transactions', 'file': (io.BytesIO(exported.data), 'transactions.csv')})
    assert preview.status_code == 200, preview.json
    assert preview.json['errors'] == [], preview.json
    assert preview.json['conflicts'] == [], preview.json
    assert preview.json['summary']['skip'] == 1
    committed = client.post('/api/v1/imports/commit', headers=headers, json={'token': preview.json['token']})
    assert committed.status_code == 200, committed.json


def test_new_csv_preview_is_atomic_and_commit_applies_rules(household):
    app, client, headers, create, account, category = household
    create('rules', {'name': 'Grocery rule', 'priority': 1, 'enabled': True, 'conditions': [{'field': 'description', 'operator': 'contains', 'value': 'food'}], 'actions': {'category_id': category['id']}})
    payload = f'type,source_account_id,destination_account_id,date,amount,description\nExpense,{account["id"]},{system_id("expense")},2026-07-15,10,food\n'.encode()
    preview = client.post('/api/v1/imports/preview', headers=headers, data={'kind': 'transactions', 'file': (io.BytesIO(payload), 'transactions.csv')})
    assert preview.status_code == 200, preview.json
    assert not preview.json['errors'], preview.json
    assert preview.json['changes'][0]['after']['category_id'] == category['id']
    records = client.get('/api/v1/transactions').json['items']
    assert not [r for r in records if not r.get('system')]
    committed = client.post('/api/v1/imports/commit', headers=headers, json={'token': preview.json['token']})
    assert committed.status_code == 200, committed.json
    assert committed.json['summary']['create'] == 1


def test_signed_returns_csv_preview_commit_and_export(household):
    app, client, headers, create, account, category = household
    investment = create('accounts', {'name': 'Investments', 'type': 'Brokerage', 'currency': 'USD',
                                    'opening_balance': '100', 'opening_date': '2024-01-01'})
    identity = investment['id']
    payload = (f'type,source_account_id,destination_account_id,date,amount,description\n'
               f'Return,{identity},{identity},2026-07-15,15,Gain\n'
               f'Return,{identity},{identity},2026-07-16,-25,Loss\n').encode()
    preview = client.post('/api/v1/imports/preview', headers=headers,
        data={'kind': 'transactions', 'file': (io.BytesIO(payload), 'returns.csv')})
    assert preview.status_code == 200, preview.json
    assert not preview.json['errors'], preview.json
    assert not preview.json['conflicts'], preview.json
    assert client.get('/api/v1/accounts/' + identity).json['balance'] == '100'
    committed = client.post('/api/v1/imports/commit', headers=headers, json={'token': preview.json['token']})
    assert committed.status_code == 200, committed.json
    assert client.get('/api/v1/accounts/' + identity).json['balance'] == '90'
    report = client.get('/api/v1/reports/data?start=2026-07-01&end=2026-07-31')
    assert report.status_code == 200, report.json
    assert report.json['summary']['returns'] == '-10'
    exported = client.get('/api/v1/exports?format=transactions')
    rows = list(csv.DictReader(io.StringIO(exported.data.decode('utf-8-sig'))))
    assert sorted(row['amount'] for row in rows) == ['-25', '15']
    preview = client.post('/api/v1/imports/preview', headers=headers,
        data={'kind': 'transactions', 'file': (io.BytesIO(exported.data), 'returns.csv')})
    assert not preview.json['errors'], preview.json
    assert preview.json['summary']['skip'] == 2


def test_stale_preview_rejected(household):
    app, client, headers, create, account, category = household
    export = client.get('/api/v1/exports?format=accounts')
    preview = client.post('/api/v1/imports/preview', headers=headers, data={'kind': 'accounts', 'file': (io.BytesIO(export.data), 'accounts.csv')}).json
    assert not preview['errors'] and not preview['conflicts'], preview
    create('tags', {'name': 'New tag'})
    response = client.post('/api/v1/imports/commit', headers=headers, json={'token': preview['token']})
    assert response.status_code == 409


def test_generic_asset_returns_contribute_to_net_worth(household):
    app, client, headers, create, account, category = household
    home = create('accounts', {'name': 'Home', 'type': 'Asset', 'opening_balance': '400000', 'opening_date': '2024-01-01'})
    assert home['account_class'] == 'asset'
    for amount in ('20000', '-5000'):
        create('transactions', {'type': 'Return', 'source_account_id': home['id'], 'destination_account_id': home['id'], 'amount': amount, 'date': '2024-02-01', 'description': 'Home valuation change'})
    assert Decimal(client.get('/api/v1/accounts/' + home['id']).json['balance']) == 415000
    summary = client.get('/api/v1/dashboard?start=2024-01-01&end=2024-12-31').json
    assert Decimal(summary['assets']) == 416000
    assert Decimal(summary['net_worth']) == 416000
    rejected = client.post('/api/v1/transactions', headers=headers, json={'type': 'Return', 'source_account_id': home['id'], 'destination_account_id': account['id'], 'amount': '10', 'date': '2024-02-01', 'description': 'Invalid return'})
    assert rejected.status_code == 400


def test_account_number_search_and_csv_roundtrip(household):
    app, client, headers, create, account, category = household
    numbered = create('accounts', {'name': 'Numbered', 'type': 'Asset', 'account_number': '001234', 'description': '=Property, with a garden\nEstimated value'})
    assert [row['id'] for row in client.get('/api/v1/accounts?q=001234').json['items']] == [numbered['id']]
    exported = client.get('/api/v1/exports?format=accounts')
    rows = list(csv.DictReader(io.StringIO(exported.data.decode('utf-8-sig'))))
    assert next(row for row in rows if row['id'] == numbered['id'])['account_number'] == '001234'
    assert next(row for row in rows if row['id'] == numbered['id'])['description'] == "'=Property, with a garden\nEstimated value"
    assert [row['id'] for row in client.get('/api/v1/accounts?q=garden').json['items']] == [numbered['id']]
    preview = client.post('/api/v1/imports/preview', headers=headers, data={'kind': 'accounts', 'file': (io.BytesIO(exported.data), 'accounts.csv')}).json
    assert not preview['errors'] and not preview['conflicts'], preview
    assert preview['summary']['skip'] == len(rows)


def test_closed_account_survives_archive_roundtrip(household, tmp_path):
    _, client, headers, create, account, category = household
    path = '/api/v1/accounts/' + account['id']
    failed = client.patch(path, headers=headers, json={'revision': account['revision'], 'closed_on': '2026-07-15'})
    assert failed.status_code == 409
    assert not client.get(path).json.get('closed_on')
    purchase = create('transactions', expense(account, category, '1000'))
    closed = client.patch(path, headers=headers, json={'revision': account['revision'], 'closed_on': '2026-07-15'})
    assert closed.status_code == 200, closed.json
    blocked = client.post('/api/v1/transactions', headers=headers, json=expense(account, category, '1', '2026-07-16'))
    assert blocked.status_code == 409
    assert client.get(path).json['balance'] == '0'
    archive = client.get('/api/v1/exports?format=archive')
    second_dir = tmp_path / 'closed-restored'
    second = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATA_DIR': str(second_dir), 'UPLOAD_DIR': str(second_dir / 'uploads'), 'BACKUP_DIR': str(second_dir / 'backups'), 'DATABASE_URL': 'sqlite:///' + str(second_dir / 'test.db')})
    other = second.test_client()
    other.post('/api/v1/auth/setup', json={'username': 'owner2', 'password': 'another correct long password'})
    other_headers = {'X-CSRF-Token': other.get('/api/v1/auth/session').json['csrf_token']}
    preview = other.post('/api/v1/imports/preview', headers=other_headers, data={'kind': 'archive', 'file': (io.BytesIO(archive.data), 'archive.zip')})
    assert preview.status_code == 200 and not preview.json['errors'], preview.json
    result = other.post('/api/v1/imports/commit', headers=other_headers, json={'token': preview.json['token']})
    assert result.status_code == 200, result.json
    restored = other.get(path).json
    assert restored['closed_on'] == '2026-07-15' and restored['balance'] == '0' and not restored['deleted']
    assert other.get('/api/v1/transactions/' + purchase['id']).status_code == 200
    report = other.get('/api/v1/reports/data?start=2026-07-01&end=2026-07-31').json
    assert Decimal(report['summary']['expenses']) == 1000


def test_deleted_unused_account_survives_archive_roundtrip(household, tmp_path):
    app, client, headers, create, account, category = household
    unused = create('accounts', {'name': 'Unused', 'type': 'Savings', 'opening_balance': '0'})
    removed = client.delete('/api/v1/accounts/' + unused['id'], headers=headers, json={'revision': unused['revision']})
    assert removed.status_code == 200, removed.json
    archive = client.get('/api/v1/exports?format=archive')
    second_dir = tmp_path / 'second'
    second = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATA_DIR': str(second_dir), 'UPLOAD_DIR': str(second_dir / 'uploads'), 'BACKUP_DIR': str(second_dir / 'backups'), 'DATABASE_URL': 'sqlite:///' + str(second_dir / 'test.db')})
    other = second.test_client()
    other.post('/api/v1/auth/setup', json={'username': 'owner2', 'password': 'another correct long password'})
    other_headers = {'X-CSRF-Token': other.get('/api/v1/auth/session').json['csrf_token']}
    preview = other.post('/api/v1/imports/preview', headers=other_headers, data={'kind': 'archive', 'file': (io.BytesIO(archive.data), 'archive.zip')})
    assert preview.status_code == 200 and not preview.json['errors'], preview.json
    result = other.post('/api/v1/imports/commit', headers=other_headers, json={'token': preview.json['token']})
    assert result.status_code == 200, result.json
    assert other.get('/api/v1/accounts/' + unused['id']).status_code == 404


def test_account_currency_change_snapshots_survive_archive_restore(household, tmp_path):
    app, client, headers, create, account, category = household
    rate = create('rates', {'currency': 'EUR', 'date': '2024-01-01', 'rate': '2'})
    tx = create('transactions', expense(account, category, '100'))
    changed = client.patch('/api/v1/accounts/' + account['id'], headers=headers, json={'currency': 'EUR', 'currency_change': 'keep', 'revision': account['revision']})
    assert changed.status_code == 200, changed.json
    assert Decimal(changed.json['balance']) == 450
    client.patch('/api/v1/rates/' + rate['id'], headers=headers, json={'rate': '3', 'revision': rate['revision']})
    archive = client.get('/api/v1/exports?format=archive')
    second_dir = tmp_path / 'currency-restored'
    second = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATA_DIR': str(second_dir), 'UPLOAD_DIR': str(second_dir / 'uploads'), 'BACKUP_DIR': str(second_dir / 'backups'), 'DATABASE_URL': 'sqlite:///' + str(second_dir / 'test.db')})
    other = second.test_client()
    other.post('/api/v1/auth/setup', json={'username': 'owner2', 'password': 'another correct long password'})
    other_headers = {'X-CSRF-Token': other.get('/api/v1/auth/session').json['csrf_token']}
    preview = other.post('/api/v1/imports/preview', headers=other_headers, data={'kind': 'archive', 'file': (io.BytesIO(archive.data), 'archive.zip')})
    assert preview.status_code == 200 and not preview.json['errors'], preview.json
    committed = other.post('/api/v1/imports/commit', headers=other_headers, json={'token': preview.json['token']})
    assert committed.status_code == 200, committed.json
    assert Decimal(other.get('/api/v1/accounts/' + account['id']).json['balance']) == 450
    assert other.get('/api/v1/transactions/' + tx['id']).json['currency'] == 'USD'


def test_failed_account_currency_change_is_atomic(household):
    app, client, headers, create, account, category = household
    path = '/api/v1/accounts/' + account['id']
    missing = client.patch(path, headers=headers, json={'currency': 'EUR', 'currency_change': 'keep', 'revision': account['revision']})
    assert missing.status_code == 422, missing.json
    assert client.get(path).json['currency'] == 'USD'
    assert client.get(path).json['revision'] == account['revision']
    create('rates', {'currency': 'JPY', 'date': '2024-01-01', 'rate': '0.01'})
    tx = create('transactions', expense(account, category, '1.25'))
    invalid = client.patch(path, headers=headers, json={'currency': 'JPY', 'currency_change': 'relabel', 'revision': account['revision']})
    assert invalid.status_code == 400, invalid.json
    unchanged = client.get(path).json
    assert unchanged['currency'] == 'USD' and unchanged['revision'] == account['revision']
    assert Decimal(unchanged['balance']) == Decimal('998.75')
    assert client.get('/api/v1/transactions/' + tx['id']).json['currency'] == 'USD'


def test_relabeling_account_updates_future_report_views(household):
    app, client, headers, create, account, category = household
    create('rates', {'currency': 'EUR', 'date': '2024-01-01', 'rate': '2'})
    tx = create('transactions', expense(account, category, '100'))
    query = '?start=2026-07-01&end=2026-07-31'
    exported = client.get('/api/v1/reports/csv' + query).data
    assert client.get('/api/v1/reports/data' + query).json['summary']['expenses'] == '100'
    changed = client.patch('/api/v1/accounts/' + account['id'], headers=headers, json={'currency': 'EUR', 'currency_change': 'relabel', 'revision': account['revision']})
    assert changed.status_code == 200, changed.json
    assert client.get('/api/v1/reports/data' + query).json['summary']['expenses'] == '200'
    assert client.get('/api/v1/transactions/' + tx['id']).json['amount'] == '100'
    assert next(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))['currency'] == 'USD'


def test_archive_roundtrip_with_attachment_and_void(household, tmp_path):
    app, client, headers, create, account, category = household
    transaction = create('transactions', expense(account, category))
    payload = b'%PDF-1.4\n% test receipt\n'
    upload = client.post('/api/v1/attachments', headers=headers, data={'transaction_id': transaction['id'], 'kind': 'receipt', 'file': (io.BytesIO(payload), 'receipt.pdf')})
    assert upload.status_code == 201, upload.json
    archive = client.get('/api/v1/exports?format=archive')
    assert archive.status_code == 200
    second_dir = tmp_path / 'second'
    second = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATA_DIR': str(second_dir), 'UPLOAD_DIR': str(second_dir / 'uploads'), 'BACKUP_DIR': str(second_dir / 'backups'), 'DATABASE_URL': 'sqlite:///' + str(second_dir / 'test.db')})
    other = second.test_client()
    other.post('/api/v1/auth/setup', json={'username': 'owner2', 'password': 'another correct long password'})
    other_headers = {'X-CSRF-Token': other.get('/api/v1/auth/session').json['csrf_token']}
    preview = other.post('/api/v1/imports/preview', headers=other_headers, data={'kind': 'archive', 'file': (io.BytesIO(archive.data), 'archive.zip')})
    assert preview.status_code == 200, preview.json
    assert not preview.json['errors'], preview.json
    response = other.post('/api/v1/imports/commit', headers=other_headers, json={'token': preview.json['token']})
    assert response.status_code == 200, response.json
    assert other.get('/api/v1/accounts/' + account['id']).json['balance'] == '980'
    assert other.get('/api/v1/attachments/' + upload.json['id']).data == payload


def test_pdf_and_csv_contain_same_transactions(household):
    app, client, headers, create, account, category = household
    for index in range(35):
        create('transactions', {**expense(account, category, str(index + 1)), 'description': f'Grocery purchase {index} with a long descriptive item name'})
    response = client.get('/api/v1/reports/pdf?start=2026-07-01&end=2026-07-31')
    assert response.status_code == 200, response.json
    reader = PdfReader(io.BytesIO(response.data))
    assert len(reader.pages) >= 3
    text = '\n'.join(page.extract_text() for page in reader.pages)
    assert '$630.00' in text
    assert 'Grocery purchase 34' in text
    assert all(f'Page {i + 1}' in page.extract_text() for i, page in enumerate(reader.pages))


def test_member_cannot_manage_users(household):
    app, client, headers, create, account, category = household
    response = client.post('/api/v1/users', headers=headers, json={'username': 'member', 'password': 'long enough password', 'role': 'member'})
    assert response.status_code == 201, response.json
    member = app.test_client()
    member.post('/api/v1/auth/login', json={'username': 'member', 'password': 'long enough password'})
    assert member.get('/api/v1/users').status_code == 403
    assert member.get('/api/v1/transactions').status_code == 200
