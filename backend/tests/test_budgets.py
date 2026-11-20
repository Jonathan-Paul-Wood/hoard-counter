from datetime import date
from decimal import Decimal

import pytest
from alembic import command
import sqlalchemy as sa

from test_analytics_exchange import household, expense
from whc.budgets import credit_count
from whc.ledger import system_id
from whc.migrations import migration_config
from whc.recurrence import occurrences


@pytest.mark.parametrize('frequency,start,end,count', [
    ('daily', '2026-01-01', '2026-01-03', 3),
    ('weekly', '2026-01-01', '2026-01-15', 3),
    ('monthly', '2026-01-31', '2026-03-30', 2),
    ('monthly', '2026-01-31', '2026-03-31', 3),
    ('quarterly', '2026-01-31', '2026-07-31', 3),
    ('annually', '2024-02-29', '2028-02-28', 4),
    ('annually', '2024-02-29', '2028-02-29', 5),
    ('daily', '2026-01-01', '2025-12-31', 0),
])
def test_anchored_credit_counts(frequency, start, end, count):
    data = dict(frequency=frequency, start_date=start)
    assert credit_count(data, date.fromisoformat(end)) == count
    assert len(occurrences(data, start, end)) == count
    data['end_date'] = start
    assert credit_count(data, date.fromisoformat(end)) == (end >= start)


def test_indefinite_daily_schedule_does_not_expand_history():
    assert credit_count(dict(frequency='daily', start_date='0001-01-01'), date.max) == date.max.toordinal()


def test_carryover_refunds_negative_allocations_and_no_postings(household):
    _, client, headers, create, account, category = household
    before = client.get('/api/v1/accounts/' + account['id']).json['balance']
    budget = create('budgets', dict(category_id=category['id'], amount='100', frequency='monthly', start_date='2026-01-31', end_date='2026-03-31'))
    assert client.get('/api/v1/accounts/' + account['id']).json['balance'] == before
    create('transactions', expense(account, category, '250', '2026-02-01'))
    summary = client.get('/api/v1/budget-summary?as_of=2026-02-28').json
    assert Decimal(summary['total_allocation']) == -50
    create('transactions', dict(source_account_id=system_id('expense'), destination_account_id=account['id'], type='Expense', date='2026-03-01', amount='25', description='Refund', category_id=category['id'], is_refund=True))
    summary = client.get('/api/v1/budget-summary?as_of=2027-01-01').json
    assert Decimal(summary['total_budget']) == 300
    assert Decimal(summary['total_actual']) == 225
    assert Decimal(summary['total_allocation']) == 75
    changed = client.patch('/api/v1/budgets/' + budget['id'], headers=headers, json=dict(revision=budget['revision'], end_date=None))
    assert changed.status_code == 200
    assert Decimal(client.get('/api/v1/budget-summary?as_of=2026-04-30').json['total_budget']) == 400
    assert client.delete('/api/v1/budgets/' + budget['id'], headers=headers, json=dict(revision=changed.json['revision'])).status_code == 200
    assert Decimal(client.get('/api/v1/budget-summary?as_of=2026-04-30').json['total_allocation']) == -225


def test_projection_combines_schedules_and_deduplicates_paid_subscriptions(household, monkeypatch):
    _, client, headers, create, account, category = household
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 1, 15))
    budget = create('budgets', dict(category_id=category['id'], amount='100', frequency='monthly', start_date='2026-01-01'))
    create('budgets', dict(category_id=category['id'], amount='25', frequency='quarterly', start_date='2026-02-01', end_date='2026-02-01'))
    sub = create('subscriptions', dict(name='Delivery', source_account_id=account['id'], destination_account_id=system_id('expense'), amount='10', currency='USD', type='Expense', start_date='2026-01-20', frequency='monthly', category_id=category['id']))
    create('transactions', expense(account, category, '20', '2026-01-10'))
    payment = create('transactions', expense(account, category, '12', '2026-02-19', subscription_id=sub['id'], subscription_date='2026-02-20'))
    create('transactions', expense(account, category, '5', '2026-03-05'))
    result = client.get(f"/api/v1/budgets/{budget['id']}/projection?date=2026-03-31")
    assert result.status_code == 200, result.json
    data = result.json
    assert Decimal(data['current_allocation']) == 80
    assert Decimal(data['future_credits']) == 225
    assert Decimal(data['recorded_expenses']) == 17
    assert Decimal(data['subscription_expenses']) == 20
    assert Decimal(data['projected_allocation']) == 268
    assert [r['date'] for r in data['occurrences']] == ['2026-01-20', '2026-03-20']
    # Voiding a payment makes the expected occurrence outstanding again.
    client.delete('/api/v1/transactions/' + payment['id'], headers=headers, json=dict(revision=payment['revision']))
    assert Decimal(client.get(f"/api/v1/budgets/{budget['id']}/projection?date=2026-03-31").json['subscription_expenses']) == 30
    same_day = client.get(f"/api/v1/budgets/{budget['id']}/projection?date=2026-01-15").json
    assert same_day['projected_allocation'] == same_day['current_allocation']
    assert client.get(f"/api/v1/budgets/{budget['id']}/projection?date=2026-01-14").status_code == 400


def test_budget_validation_and_legacy_input(household):
    _, client, headers, create, _, category = household
    payload = dict(category_id=category['id'], amount='10', start_date='2026-01-01', frequency='weekly')
    for change in (dict(amount='-1'), dict(frequency='yearly'), dict(start_date='bad'), dict(end_date='2025-12-31'), dict(category_id=None)):
        assert client.post('/api/v1/budgets', headers=headers, json={**payload, **change}).status_code == 400
    legacy = create('budgets', dict(category_id=category['id'], amount='10', month='2024-02'))
    assert legacy['start_date'] == '2024-02-01'
    assert legacy['end_date'] == '2024-02-29'
    assert 'month' not in legacy
    assert client.get('/api/v1/budget-summary?as_of=bad').status_code == 400


def test_projection_fx_warnings_and_subscription_scope(household, monkeypatch):
    _, client, _, create, account, category = household
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 1, 1))
    budget = create('budgets', dict(category_id=category['id'], amount='100', frequency='monthly', start_date='2026-01-01'))
    base = dict(name='Foreign payment', source_account_id=account['id'], destination_account_id=system_id('expense'), amount='10', currency='EUR', type='Expense', start_date='2026-01-15', frequency='monthly', end_date='2026-01-15', category_id=category['id'])
    create('subscriptions', base)
    create('subscriptions', {**base, 'currency': 'USD', 'active': False})
    other = create('categories', dict(name='Other'))
    create('subscriptions', {**base, 'currency': 'USD', 'category_id': other['id']})
    endpoint = f"/api/v1/budgets/{budget['id']}/projection?date=2026-02-28"
    data = client.get(endpoint).json
    assert data['incomplete'] and data['warnings']
    assert Decimal(data['subscription_expenses']) == 0
    create('rates', dict(currency='EUR', date='2025-12-31', rate='1.25'))
    # Future rates must not be used to value today's projection.
    create('rates', dict(currency='EUR', date='2026-02-01', rate='2'))
    data = client.get(endpoint).json
    assert not data['incomplete'] and not data['warnings']
    assert Decimal(data['subscription_expenses']) == Decimal('12.50')
    assert Decimal(data['projected_allocation']) == Decimal('187.50')


def test_report_allocations_are_cumulative_and_stop_at_report_end(household):
    _, client, _, create, account, category = household
    create('budgets', dict(category_id=category['id'], amount='10', frequency='daily', start_date='2026-01-01'))
    create('transactions', expense(account, category, '5', '2025-12-31'))
    create('transactions', expense(account, category, '20', '2026-01-05'))
    other = create('accounts', dict(name='Savings', type='Savings', currency='USD'))
    create('transactions', dict(type='Transfer', date='2026-01-03', source_account_id=account['id'], destination_account_id=other['id'], amount='50', description='Move cash', category_id=category['id']))
    data = client.get('/api/v1/reports/data?start=2026-01-03&end=2026-01-04').json
    snapshot = data['budgets'][0]
    assert snapshot['as_of'] == '2026-01-04'
    assert Decimal(snapshot['total_budget']) == 40
    assert Decimal(snapshot['total_actual']) == 5
    assert Decimal(snapshot['total_allocation']) == 35


def test_upgrade_preserves_original_monthly_credits(tmp_path):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'migration.db').as_posix())
    config = migration_config()
    config.attributes['engine'] = engine
    command.upgrade(config, '0002_migration_runs')
    records = sa.table('records', sa.column('id', sa.String), sa.column('kind', sa.String), sa.column('data', sa.JSON), sa.column('revision', sa.Integer), sa.column('last_modified', sa.String), sa.column('deleted', sa.Boolean))
    with engine.begin() as connection:
        connection.execute(records.insert().values(id='budget', kind='budget', data=dict(month='2024-02', category_id='category', amount='100'), revision=7, last_modified='original', deleted=False))
    command.upgrade(config, 'head')
    with engine.connect() as connection:
        record = connection.execute(sa.select(records).where(records.c.id == 'budget')).mappings().one()
    assert record['revision'] == 7
    assert record['last_modified'] == 'original'
    assert record['data'] == dict(start_date='2024-02-01', end_date='2024-02-29', frequency='monthly', category_id='category', amount='100')
