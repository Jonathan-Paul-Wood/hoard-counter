from datetime import date
from decimal import Decimal as D

import pytest

from whc import create_app
from whc.models import db
from whc.ledger import save_record, void_record, system_id, reconcile_account
from whc.analytics import performance, forecast, balance_summary, project, transaction_records, planned_flow_occurrences
from whc.reports import report_data
from whc.validation import ApiError


@pytest.fixture
def household(tmp_path, monkeypatch):
    app = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATABASE_URL': 'sqlite:///:memory:',
                      'DATA_DIR': str(tmp_path), 'UPLOAD_DIR': str(tmp_path / 'uploads'), 'BACKUP_DIR': str(tmp_path / 'backups')})
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 1, 1))
    with app.app_context():
        yield app
        db.session.remove()
        db.drop_all()


def account(name='Portfolio', amount='100', currency='USD', typ='Brokerage'):
    return save_record('account', {'name': name, 'type': typ, 'currency': currency,
                                  'opening_balance': amount, 'opening_date': '2023-01-01'}, None)


def valuation(account, when, amount):
    return save_record('valuation', {'account_id': account.id, 'date': when, 'amount': amount}, None)


def transaction(source, destination, amount, when, typ='Transfer', **kwargs):
    return save_record('transaction', {'source_account_id': source, 'destination_account_id': destination,
                       'amount': amount, 'date': when, 'type': typ, 'description': 'Example', **kwargs}, None)


def test_performance_valuation_gain_and_fee_count_once(household):
    portfolio = account()
    valuation(portfolio, '2024-01-01', '100')
    transaction(portfolio.id, system_id('expense'), '2', '2024-06-02', 'Expense', investment_fee=True)
    valuation(portfolio, '2025-01-01', '108')
    result = performance(portfolio.id)
    assert D(result['cumulative_return']) == D('.08')
    assert D(result['intervals'][0]['external_flows']) == 0


def test_reconciliation_is_external_capital_not_a_gain(household):
    portfolio = account()
    valuation(portfolio, '2024-01-01', '100')
    reconcile_account(portfolio.id, '120', '2024-07-01', None, portfolio.revision)
    valuation(portfolio, '2025-01-01', '130')
    result = performance(portfolio.id)
    weight = D((date(2025, 1, 1) - date(2024, 7, 1)).days) / D(366)
    assert abs(D(result['cumulative_return']) - D(10) / (D(100) + weight * 20)) < D('1e-90')
    assert D(result['intervals'][0]['external_flows']) == 20
    flow = result['intervals'][0]['cash_flows'][0]
    assert flow['kind'] == 'Balance correction'
    assert D(flow['amount']) == 20
    assert D(flow['weight']) == weight


def test_performance_shows_signed_flows_weights_and_period_calculations(household):
    portfolio = account()
    cash = account('Cash', '1000', typ='Checking')
    transaction(cash.id, portfolio.id, '10', '2024-01-01')
    valuation(portfolio, '2024-01-01', '100')
    contribution = transaction(cash.id, portfolio.id, '40', '2024-01-06')
    withdrawal = transaction(portfolio.id, cash.id, '10', '2024-01-11')
    transaction(portfolio.id, system_id('expense'), '2', '2024-01-08', 'Expense', investment_fee=True)
    valuation(portfolio, '2024-01-11', '142')
    valuation(portfolio, '2024-01-21', '156.2')

    result = performance(portfolio.id)
    first, second = result['intervals']
    assert first['days'] == 10
    assert D(first['starting_value']) == 100
    assert D(first['ending_value']) == 142
    assert D(first['external_flows']) == 30
    assert D(first['weighted_flows']) == 20
    assert D(first['gain']) == 12
    assert D(first['weighted_capital']) == 120
    assert D(first['return_rate']) == D('.1')
    flows = first['cash_flows']
    assert [flow['id'] for flow in flows] == [contribution.id, withdrawal.id]
    assert [flow['remaining_days'] for flow in flows] == [5, 0]
    assert [D(flow['amount']) for flow in flows] == [40, -10]
    assert [D(flow['weight']) for flow in flows] == [D('.5'), 0]
    assert [D(flow['weighted_amount']) for flow in flows] == [20, 0]
    assert second['cash_flows'] == []
    assert D(second['weighted_flows']) == 0
    assert D(second['return_rate']) == D('.1')
    assert D(result['cumulative_return']) == D('.21')
    assert result['annualized_return'] is None


def test_valuation_loss_is_not_an_external_flow(household):
    portfolio = account()
    valuation(portfolio, '2024-01-01', '100')
    valuation(portfolio, '2025-01-01', '88')
    result = performance(portfolio.id)
    assert D(result['cumulative_return']) == D('-.12')
    assert D(result['intervals'][0]['external_flows']) == 0


def test_multiyear_fx_snapshots_and_future_values(household):
    save_record('rate', {'currency': 'EUR', 'date': '2023-01-01', 'rate': '1'}, None)
    portfolio = account(currency='EUR')
    valuation(portfolio, '2023-01-01', '100')
    middle_rate = save_record('rate', {'currency': 'EUR', 'date': '2024-01-01', 'rate': '1.1'}, None)
    valuation(portfolio, '2024-01-01', '100')
    save_record('rate', {'currency': 'EUR', 'date': '2025-01-01', 'rate': '1.21'}, None)
    valuation(portfolio, '2025-01-01', '100')
    save_record('rate', {'rate': '9'}, None, middle_rate.id, middle_rate.revision)
    valuation(portfolio, '2027-01-01', '1000')
    result = performance(portfolio.id)
    assert result['end'] == '2025-01-01'
    assert D(result['cumulative_return']) == D('.21')
    expected = D('1.21') ** (D('365.25') / D(731)) - 1
    assert D(result['annualized_return']) == expected


def test_total_loss_is_reported_but_not_projected(household):
    portfolio = account()
    valuation(portfolio, '2024-01-01', '100')
    valuation(portfolio, '2025-01-01', '0')
    result = performance(portfolio.id)
    assert result['available'] is True
    assert D(result['cumulative_return']) == -1
    assert D(result['annualized_return']) == -1
    with pytest.raises(ValueError, match='greater than -100%'):
        project({'date': '2027-01-01', 'assumptions': [{'account_id': portfolio.id, 'mode': 'historical'}]})


def test_forecast_only_complete_calendar_months_and_zero_months(household, monkeypatch):
    monkeypatch.setattr('whc.analytics.today', lambda: date(2026, 10, 16))
    checking = account('Checking', '1000', typ='Checking')
    food = save_record('category', {'name': 'Food'}, None)
    transaction(checking.id, system_id('expense'), '90', '2026-07-01', 'Expense', category_id=food.id)
    transaction(checking.id, system_id('expense'), '900', '2026-10-01', 'Expense', category_id=food.id)
    transaction(checking.id, system_id('expense'), '900', '2026-11-01', 'Expense', category_id=food.id)
    result = forecast('quarter')
    assert result['history_months'] == ['2026-07', '2026-08', '2026-09']
    assert result['start'] == '2027-01-01'
    assert result['end'] == '2027-03-31'
    assert D(result['nonrecurring']) == 90


def test_report_filters_and_frozen_transaction_fx(household):
    rate = save_record('rate', {'currency': 'EUR', 'date': '2023-01-01', 'rate': '1.2'}, None)
    euros = account('Euros', currency='EUR', typ='Savings')
    payee = account('Payee', '0', 'EUR', 'Expense Payee')
    food = save_record('category', {'name': 'Food'}, None)
    tag = save_record('tag', {'name': 'Holiday'}, None)
    good = transaction(euros.id, payee.id, '10', '2025-06-01', 'Expense', category_id=food.id, tag_ids=[tag.id])
    transaction(euros.id, payee.id, '20', '2025-06-02', 'Expense', category_id=food.id)
    save_record('rate', {'rate': '2'}, None, rate.id, rate.revision)
    filters = {'start': '2025-06-01', 'end': '2025-06-30', 'account_id': euros.id, 'account_type': 'Savings',
               'currency': 'EUR', 'type': 'Expense', 'category_id': food.id, 'tag_id': tag.id}
    report = report_data(filters)
    assert [row['id'] for row in report['transactions']] == [good.id]
    assert D(report['summary']['expenses']) == 12
    assert D(report['balance_summary']['assets']) == 140
    assert not transaction_records({**filters, 'type': 'Income'})


def test_missing_fx_marks_partial_projection_and_zero_needs_no_rate(household):
    empty = account('Empty EUR', '0', 'EUR')
    assert balance_summary(date(2026, 1, 1))['incomplete'] is False
    rate = save_record('rate', {'currency': 'EUR', 'date': '2023-01-01', 'rate': '1.2'}, None)
    foreign = account('Foreign', '100', 'EUR')
    cash = account('Cash', '50', typ='Checking')
    void_record('rate', rate.id, None, rate.revision)
    result = balance_summary(date(2026, 1, 1))
    assert result['incomplete'] is True
    row = next(r for r in result['accounts'] if r['id'] == foreign.id)
    assert row['balance_usd'] is None and row['signed_balance_usd'] is None
    assert D(result['net_worth']) == 50
    projected = project({'date': '2027-01-01', 'assumptions': [], 'flows': []})
    assert projected['incomplete'] is True
    assert D(projected['total']) == 50


def test_projection_signed_flows_and_end_of_day_timing(household):
    portfolio = account('Cash', '100', typ='Checking')
    result = project({'date': '2027-01-01', 'assumptions': [{'account_id': portfolio.id, 'annual_rate': '0'}],
                      'flows': [{'account_id': portfolio.id, 'date': '2026-07-01', 'amount': '-30.00'},
                                {'account_id': portfolio.id, 'date': '2027-01-01', 'amount': '5.00'}]})
    assert D(result['total']) == 75
    assert D(result['series'][0]['total']) == 100
    growing = project({'date': '2027-01-01', 'assumptions': [{'account_id': portfolio.id, 'annual_rate': '0.1'}],
                       'flows': [{'account_id': portfolio.id, 'date': '2027-01-01', 'amount': '5.00'}]})
    assert D(growing['total']) == D(100) * D('1.1') ** (D(365) / D('365.25')) + 5


@pytest.mark.parametrize(('frequency', 'start', 'end', 'expected'), [
    ('daily', '2026-01-02', '2026-01-04', ['2026-01-02', '2026-01-03', '2026-01-04']),
    ('weekly', '2026-01-02', '2026-01-16', ['2026-01-02', '2026-01-09', '2026-01-16']),
    ('monthly', '2026-01-31', '2026-04-30', ['2026-01-31', '2026-02-28', '2026-03-31', '2026-04-30']),
    ('quarterly', '2026-01-31', '2026-07-31', ['2026-01-31', '2026-04-30', '2026-07-31']),
    ('yearly', '2028-02-29', '2032-02-29', ['2028-02-29', '2029-02-28', '2030-02-28', '2031-02-28', '2032-02-29']),
])
def test_planned_flow_frequencies_are_anchored_and_end_inclusive(frequency, start, end, expected):
    flows = planned_flow_occurrences([dict(account_id='portfolio', date=start, end_date=end,
                                          frequency=frequency, amount='10')], date(2026, 1, 1), date(2033, 1, 1))
    assert [flow['date'] for flow in flows] == expected


def test_recurring_projection_matches_dated_flows_and_saved_scenario(household):
    portfolio = account()
    body = dict(date='2026-05-15', assumptions=[dict(account_id=portfolio.id, annual_rate='0.12')], flows=[
        dict(account_id=portfolio.id, date='2025-12-31', end_date='2026-06-30', frequency='monthly', amount='20'),
        dict(account_id=portfolio.id, date='2026-02-28', end_date='2026-03-31', frequency='monthly', amount='-5'),
        dict(account_id=portfolio.id, date='2026-05-15', amount='7'),
    ])
    scenario = save_record('scenario', dict(name='Recurring plan', **body), None)
    result = project(scenario.data)
    explicit = [dict(account_id=portfolio.id, date=day, amount=amount) for day, amount in [
        ('2026-01-31', '20'), ('2026-02-28', '20'), ('2026-03-31', '20'), ('2026-04-30', '20'),
        ('2026-02-28', '-5'), ('2026-03-28', '-5'), ('2026-05-15', '7'),
    ]]
    assert result == project({**body, 'flows': explicit})
    assert D(result['series'][0]['total']) == 100
    assert scenario.data['flows'] == body['flows']


@pytest.mark.parametrize('changes', [
    {'frequency': 'unsupported'}, {'end_date': None}, {'end_date': 'invalid'},
    {'end_date': '2026-01-01'}, {'date': '2028-01-01', 'end_date': '2028-02-01'},
    {'amount': 'NaN'},
])
def test_recurring_projection_validates_schedule(household, changes):
    portfolio = account()
    flow = dict(account_id=portfolio.id, date='2026-01-31', end_date='2026-12-31', frequency='monthly', amount='10')
    with pytest.raises((ValueError, ApiError)):
        project(dict(date='2027-01-01', flows=[{**flow, **changes}]))


def test_recurring_flows_skip_today_and_expired_schedules():
    flow = dict(account_id='portfolio', date='2025-12-01', end_date='2026-02-01', frequency='monthly', amount='10')
    assert [f['date'] for f in planned_flow_occurrences([flow], date(2026, 1, 1), date(2026, 3, 1))] == ['2026-02-01']
    assert planned_flow_occurrences([flow], date(2026, 2, 1), date(2026, 3, 1)) == []


def test_recurring_flows_enforce_combined_occurrence_limit():
    flow = dict(account_id='portfolio', date='2026-01-02', end_date='2045-01-01', frequency='daily', amount='1')
    with pytest.raises(ValueError, match='10000 planned flow occurrences'):
        planned_flow_occurrences([flow, flow], date(2026, 1, 1), date(2045, 1, 1))


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '-Infinity', True, 0.2, '1e9999', None])
def test_projection_rejects_invalid_annual_rates(household, bad):
    portfolio = account()
    with pytest.raises((ValueError, ApiError)):
        project({'date': '2027-01-01', 'assumptions': [{'account_id': portfolio.id, 'annual_rate': bad}]})


@pytest.mark.parametrize('body', [None, [], {'date': '2027-01-01', 'assumptions': True},
                                 {'date': '2027-01-01', 'flows': [False]},
                                 {'date': '2027-01-01', 'flows': [{'account_id': [], 'date': '2026-04-01', 'amount': '2'}]}])
def test_malformed_projection_shapes_are_validation_errors(household, body):
    with pytest.raises(ValueError):
        project(body)
