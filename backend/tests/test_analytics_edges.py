from datetime import date
from decimal import Decimal as D

import pytest

from whc import create_app
from whc.models import db
from whc.ledger import save_record, void_record, system_id, reconcile_account
from whc.analytics import performance, forecast, balance_summary, project, transaction_records
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


def test_performance_cash_return_and_fee_count_once(household):
    portfolio = account()
    valuation(portfolio, '2024-01-01', '100')
    transaction(system_id('income'), portfolio.id, '5', '2024-06-01', 'Return')
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
