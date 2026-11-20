from datetime import date
from decimal import Decimal

import pytest
from alembic import command
import sqlalchemy as sa

from test_analytics_exchange import household, expense
from whc.budgets import credit_count
from whc.ledger import system_id
from whc.migrations import migration_config


@pytest.mark.parametrize('period,frequency,start,end,count', [
    ('day', 1, '2026-01-01', '2026-01-03', 3),
    ('week', 1, '2026-01-01', '2026-01-15', 3),
    ('week', 2, '2026-01-01', '2026-01-28', 2),
    ('day', 3, '2026-01-01', '2026-01-07', 3),
    ('year', 2, '2024-02-29', '2028-02-28', 2),
    ('month', 1, '2026-01-31', '2026-03-30', 2),
    ('month', 1, '2026-01-31', '2026-03-31', 3),
    ('month', 3, '2026-01-31', '2026-07-31', 3),
    ('year', 1, '2024-02-29', '2028-02-28', 4),
    ('year', 1, '2024-02-29', '2028-02-29', 5),
    ('day', 1, '2026-01-01', '2025-12-31', 0),
])
def test_anchored_credit_counts(period, frequency, start, end, count):
    data = dict(period=period, frequency=frequency, start_date=start)
    assert credit_count(data, date.fromisoformat(end)) == count
    data['end_date'] = start
    assert credit_count(data, date.fromisoformat(end)) == (end >= start)


def test_indefinite_daily_schedule_does_not_expand_history():
    assert credit_count(dict(period='day', frequency=1, start_date='0001-01-01'), date.max) == date.max.toordinal()


def test_monthly_progress_uses_full_month_credits_and_month_to_date_expenses(household):
    _, client, _, create, account, category = household
    create('budgets', dict(category_id=category['id'], amount='5', period='day', frequency=1,
                           start_date='2026-08-01', end_date='2026-09-30'))
    create('transactions', expense(account, category, '40', '2026-08-31'))
    create('transactions', expense(account, category, '30', '2026-09-02'))
    create('transactions', dict(source_account_id=system_id('expense'), destination_account_id=account['id'],
                                type='Expense', date='2026-09-03', amount='5', description='Refund',
                                category_id=category['id'], is_refund=True))
    create('transactions', expense(account, category, '100', '2026-09-25'))
    item = client.get('/api/v1/budget-summary?as_of=2026-09-10').json['items'][0]
    assert Decimal(item['monthly_budget']) == 150
    assert Decimal(item['monthly_actual']) == 25
    assert Decimal(item['budget']) == 205
    assert Decimal(item['actual']) == 65
    # Ended budgets retain allocations, but no longer contribute monthly credits.
    item = client.get('/api/v1/budget-summary?as_of=2026-10-10').json['items'][0]
    assert Decimal(item['monthly_budget']) == 0
    assert Decimal(item['monthly_actual']) == 0
    assert Decimal(item['budget']) == 305


def test_monthly_progress_counts_anchored_occurrences_across_schedule_changes(household):
    _, client, _, create, _, category = household
    create('budgets', dict(category_id=category['id'], amount='100', period='month', frequency=1,
                           start_date='2026-01-31', end_date='2026-02-28'))
    create('budgets', dict(category_id=category['id'], amount='10', period='week', frequency=2,
                           start_date='2026-03-01', end_date='2026-03-15'))
    february = client.get('/api/v1/budget-summary?as_of=2026-02-01').json['items'][0]
    assert Decimal(february['monthly_budget']) == 100
    march = client.get('/api/v1/budget-summary?as_of=2026-03-01').json['items'][0]
    assert Decimal(march['monthly_budget']) == 20


def test_carryover_refunds_negative_allocations_and_no_postings(household):
    _, client, headers, create, account, category = household
    before = client.get('/api/v1/accounts/' + account['id']).json['balance']
    budget = create('budgets', dict(category_id=category['id'], amount='100', period='month', frequency=1, start_date='2026-01-31', end_date='2026-03-31'))
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
    assert Decimal(client.get('/api/v1/budget-summary?as_of=2026-04-30').json['total_allocation']) == 0


def test_projection_combines_schedules_and_deduplicates_paid_subscriptions(household, monkeypatch):
    _, client, headers, create, account, category = household
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 1, 15))
    budget = create('budgets', dict(category_id=category['id'], amount='100', period='month', frequency=1, start_date='2026-01-01', end_date='2026-01-31'))
    create('budgets', dict(category_id=category['id'], amount='25', period='month', frequency=3, start_date='2026-02-01', end_date='2026-02-01'))
    sub = create('subscriptions', dict(name='Delivery', source_account_id=account['id'], destination_account_id=system_id('expense'), amount='10', currency='USD', type='Expense', start_date='2026-01-20', frequency='monthly', category_id=category['id']))
    create('transactions', expense(account, category, '20', '2026-01-10'))
    payment = create('transactions', expense(account, category, '12', '2026-02-19', subscription_id=sub['id'], subscription_date='2026-02-20'))
    create('transactions', expense(account, category, '5', '2026-03-05'))
    result = client.get(f"/api/v1/budgets/{budget['id']}/projection?date=2026-03-31")
    assert result.status_code == 200, result.json
    data = result.json
    assert Decimal(data['current_allocation']) == 80
    assert Decimal(data['future_credits']) == 25
    assert Decimal(data['recorded_expenses']) == 17
    assert Decimal(data['subscription_expenses']) == 20
    assert Decimal(data['projected_allocation']) == 68
    assert [r['date'] for r in data['occurrences']] == ['2026-01-20', '2026-03-20']
    # Voiding a payment makes the expected occurrence outstanding again.
    client.delete('/api/v1/transactions/' + payment['id'], headers=headers, json=dict(revision=payment['revision']))
    assert Decimal(client.get(f"/api/v1/budgets/{budget['id']}/projection?date=2026-03-31").json['subscription_expenses']) == 30
    assert client.get(f"/api/v1/budgets/{budget['id']}/projection?date=2026-01-15").status_code == 400
    assert client.get(f"/api/v1/budgets/{budget['id']}/projection?date=2026-01-14").status_code == 400


def test_budget_validation_and_overlap(household):
    _, client, headers, create, _, category = household
    payload = dict(category_id=category['id'], amount='10', start_date='2026-01-01', period='week', frequency=2)
    for change in (dict(amount='-1'), dict(period='yearly'), dict(frequency=0), dict(frequency=-1), dict(frequency=True), dict(frequency=1.5), dict(frequency='2'), dict(start_date='bad'), dict(end_date='2025-12-31'), dict(category_id=None)):
        assert client.post('/api/v1/budgets', headers=headers, json={**payload, **change}).status_code == 400
    first = create('budgets', {**payload, 'end_date': '2026-02-01'})
    assert client.post('/api/v1/budgets', headers=headers, json={**payload, 'start_date': '2026-02-01'}).status_code == 409
    second = create('budgets', {**payload, 'start_date': '2026-02-02'})
    response = client.patch('/api/v1/budgets/' + first['id'], headers=headers, json=dict(revision=first['revision'], end_date=None))
    assert response.status_code == 409
    assert client.patch('/api/v1/budgets/' + second['id'], headers=headers, json=dict(revision=second['revision'], amount='20')).status_code == 200
    assert client.get('/api/v1/budget-summary?as_of=bad').status_code == 400


def test_projection_fx_warnings_and_subscription_scope(household, monkeypatch):
    _, client, _, create, account, category = household
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 1, 1))
    budget = create('budgets', dict(category_id=category['id'], amount='100', period='month', frequency=1, start_date='2026-01-01'))
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
    create('budgets', dict(category_id=category['id'], amount='10', period='day', frequency=1, start_date='2026-01-01'))
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


def test_upgrade_resets_budget_data(tmp_path):
    engine = sa.create_engine('sqlite:///' + (tmp_path / 'migration.db').as_posix())
    config = migration_config()
    config.attributes['engine'] = engine
    command.upgrade(config, '0003_recurring_budgets')
    metadata = sa.MetaData()
    records = sa.Table('records', metadata, autoload_with=engine)
    audit = sa.Table('audit_events', metadata, autoload_with=engine)
    with engine.begin() as connection:
        for identity, kind, deleted in [('budget', 'budget', False), ('deleted-budget', 'budget', True), ('category', 'category', False)]:
            connection.execute(records.insert().values(id=identity, kind=kind, data=dict(name=identity), revision=7, last_modified='original', deleted=deleted))
            connection.execute(audit.insert().values(id=identity, record_id=identity, kind=kind, action='create', origin='manual', after=dict(name=identity), created_at='original'))
    command.upgrade(config, 'head')
    command.upgrade(config, 'head')
    with engine.connect() as connection:
        assert connection.execute(sa.select(records.c.id).where(records.c.kind == 'budget')).all() == []
        assert connection.execute(sa.select(audit.c.id)).scalars().all() == ['category']
        assert connection.execute(sa.select(records.c.revision).where(records.c.id == 'category')).scalar_one() == 7
        assert connection.execute(sa.select(sa.func.count()).select_from(records).where(records.c.kind == 'account')).scalar_one() == 6


def test_projection_selection_and_untracked_categories(household, monkeypatch):
    _, client, headers, create, account, category = household
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 1, 15))
    first = create('budgets', dict(category_id=category['id'], amount='100', period='month', frequency=1, start_date='2026-01-01', end_date='2026-01-31'))
    second = create('budgets', dict(category_id=category['id'], amount='50', period='week', frequency=2, start_date='2026-02-01'))
    other = create('categories', dict(name='Untracked'))
    create('transactions', expense(account, other, '999', '2026-01-10'))
    create('transactions', expense(account, category, '20', '2026-01-10'))
    summary = client.get('/api/v1/budget-summary?as_of=2026-01-15').json
    assert len(summary['items']) == 1
    assert Decimal(summary['total_allocation']) == 80
    def project(ids):
        return client.post('/api/v1/budget-projections', headers=headers, json=dict(date='2026-02-28', budget_ids=ids))
    before = client.get('/api/v1/budgets').json
    assert project([]).json['items'] == []
    assert Decimal(project([first['id']]).json['items'][0]['projected_allocation']) == 80
    result = project([first['id'], second['id'], second['id']]).json
    assert len(result['items']) == 1
    assert Decimal(result['items'][0]['projected_allocation']) == 180
    assert client.get('/api/v1/budgets').json == before
    assert client.post('/api/v1/budget-projections', headers=headers, json=dict(date='2026-01-15', budget_ids=[])).status_code == 400
    assert project(['missing']).status_code == 404


def test_subscription_payment_after_target_does_not_hide_planned_expense(household, monkeypatch):
    _, client, headers, create, account, category = household
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 1, 1))
    budget = create('budgets', dict(category_id=category['id'], amount='100', period='month', frequency=1, start_date='2026-01-01'))
    sub = create('subscriptions', dict(name='Delivery', source_account_id=account['id'], destination_account_id=system_id('expense'), amount='10', currency='USD', type='Expense', start_date='2026-01-31', frequency='monthly', category_id=category['id']))
    create('transactions', expense(account, category, '10', '2026-02-01', subscription_id=sub['id'], subscription_date='2026-01-31'))
    result = client.post('/api/v1/budget-projections', headers=headers, json=dict(date='2026-01-31', budget_ids=[budget['id']])).json['items'][0]
    assert Decimal(result['subscription_expenses']) == 10
    assert Decimal(result['recorded_expenses']) == 0
    assert Decimal(result['projected_allocation']) == 90
