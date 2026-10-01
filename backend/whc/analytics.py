"""Shared exact-decimal reporting, forecasts and investment performance."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, request, g

from .auth import login_required
from .models import db, Record
from .ledger import list_records, get_record, account_balance, rate_for, public_record, save_record
from .recurrence import occurrences
from .validation import ApiError, date_string, decimal as input_decimal, amount as input_amount

bp = Blueprint('analytics', __name__)
D = Decimal
ZERO = D('0')


def dec(value):
    if isinstance(value, (float, bool)):
        raise ValueError('Financial values must be exact decimal strings.')
    result = D(str(value or '0'))
    if not result.is_finite():
        raise ValueError('A finite decimal value is required.')
    return result


def text(value):
    return format(value, 'f')


def today():
    return datetime.now(ZoneInfo(current_app.config.get('HOUSEHOLD_TIMEZONE', 'America/New_York'))).date()


def month_start(value):
    return value.replace(day=1)


def add_months(value, count):
    index = value.year * 12 + value.month - 1 + count
    year, month = divmod(index, 12)
    return date(year, month + 1, min(value.day, calendar.monthrange(year, month + 1)[1]))


def transaction_records(filters=None):
    filters = filters or {}
    for key in ('start', 'end'):
        if filters.get(key):
            date_string(filters[key], key)
    if filters.get('start') and filters.get('end') and filters['start'] > filters['end']:
        raise ValueError('The start date must be on or before the end date.')
    accounts = {r.id: r.data for r in list_records('account')}
    result = []
    for record in list_records('transaction'):
        row = record.to_dict()
        if row.get('system'):
            continue
        dt = row.get('date', '')
        if filters.get('start') and dt < filters['start']:
            continue
        if filters.get('end') and dt > filters['end']:
            continue
        pair = [row.get('source_account_id'), row.get('destination_account_id')]
        if filters.get('account_id') and filters['account_id'] not in pair:
            continue
        if any(filters.get(key) and row.get(key) != filters[key] for key in ('type', 'category_id', 'subscription_id')):
            continue
        if filters.get('tag_id') and filters['tag_id'] not in row.get('tag_ids', []):
            continue
        if filters.get('account_type') and not any(accounts.get(i, {}).get('type') == filters['account_type'] for i in pair):
            continue
        if filters.get('currency') and not any(accounts.get(i, {}).get('currency') == filters['currency'] for i in pair):
            continue
        result.append(row)
    return sorted(result, key=lambda r: (r['date'], r['id']), reverse=True)


def expense_amount(row):
    if row.get('type') != 'Expense':
        return ZERO
    return dec(row.get('usd_amount')) * (-1 if row.get('is_refund') else 1)


def summarize(rows):
    categories = {r.id: r.data.get('name', '') for r in list_records('category')}
    groups, months = {}, {}
    income = expenses = returns = ZERO
    for row in rows:
        amount = dec(row.get('usd_amount'))
        month = row['date'][:7]
        bucket = months.setdefault(month, dict(income=ZERO, expenses=ZERO, returns=ZERO))
        if row.get('type') == 'Expense':
            amount = expense_amount(row)
            expenses += amount
            bucket['expenses'] += amount
            key = row.get('category_id')
            groups[key] = groups.get(key, ZERO) + amount
        elif row.get('type') == 'Income':
            income += amount
            bucket['income'] += amount
        elif row.get('type') == 'Return':
            returns += amount
            bucket['returns'] += amount
    return {
        'summary': dict(income=text(income), expenses=text(expenses), returns=text(returns), net=text(income + returns - expenses)),
        'category_spending': [dict(category_id=k, name=categories.get(k, 'Uncategorized'), amount=text(v)) for k, v in sorted(groups.items(), key=lambda kv: kv[1], reverse=True)],
        'monthly_trend': [dict(month=k, **{f: text(v) for f, v in b.items()}, net=text(b['income'] + b['returns'] - b['expenses'])) for k, b in sorted(months.items())],
    }


def balance_summary(as_of=None, filters=None):
    as_of = as_of or today()
    filters = filters or {}
    assets = liabilities = ZERO
    accounts, warnings = [], []
    for record in list_records('account'):
        if record.data.get('account_class') not in ('asset', 'liability'):
            continue
        if filters.get('account_id') and record.id != filters['account_id']:
            continue
        if filters.get('account_type') and record.data.get('type') != filters['account_type']:
            continue
        if filters.get('currency') and record.data.get('currency') != filters['currency']:
            continue
        native = account_balance(record.id, through=as_of.isoformat())
        row = record.to_dict()
        owed = record.data.get('account_class') == 'liability'
        row['balance'] = text(-native if owed else native)
        row['signed_balance'] = text(native)
        try:
            usd = native * rate_for(record.data.get('currency', 'USD'), as_of.isoformat()) if native else ZERO
        except (ValueError, LookupError, ApiError) as exc:
            row['balance_usd'] = None
            row['signed_balance_usd'] = None
            row['missing_rate'] = True
            warnings.append(f"{row['name']}: {exc}")
            accounts.append(row)
            continue
        row['balance_usd'] = text(-usd if owed else usd)
        row['signed_balance_usd'] = text(usd)
        row['missing_rate'] = False
        if owed:
            liabilities -= usd
        else:
            assets += usd
        accounts.append(row)
    return dict(assets=text(assets), liabilities=text(liabilities), net_worth=text(assets - liabilities), accounts=accounts, warnings=warnings, incomplete=bool(warnings))


@bp.get('/dashboard')
@login_required
def dashboard():
    args = request.args.to_dict()
    args.setdefault('start', month_start(today()).isoformat())
    args.setdefault('end', today().isoformat())
    summary = summarize(transaction_records(args))
    balances = balance_summary(date.fromisoformat(args['end']), args)
    return jsonify(**balances, **summary['summary'], category_spending=summary['category_spending'], monthly_trend=summary['monthly_trend'], start=args['start'], end=args['end'])


def budget_summary(month):
    start = date.fromisoformat(month + '-01')
    end = add_months(start, 1) - timedelta(days=1)
    actuals = {r['category_id']: dec(r['amount']) for r in summarize(transaction_records({'start': start.isoformat(), 'end': end.isoformat()}))['category_spending']}
    budgets = {r.data.get('category_id'): dec(r.data['amount']) for r in list_records('budget') if r.data.get('month') == month}
    names = {r.id: r.data['name'] for r in list_records('category')}
    items = []
    for key in set(actuals) | set(budgets):
        budget, actual = budgets.get(key, ZERO), actuals.get(key, ZERO)
        items.append(dict(category_id=key, name=names.get(key, 'Uncategorized'), budget=text(budget), actual=text(actual), remaining=text(budget - actual)))
    return dict(month=month, items=sorted(items, key=lambda r: r['name']), total_budget=text(sum(budgets.values(), ZERO)), total_actual=text(sum(actuals.values(), ZERO)))


@bp.get('/budget-summary')
@login_required
def budgets_summary():
    return jsonify(budget_summary(request.args.get('month', today().strftime('%Y-%m'))))


@bp.post('/budgets/copy')
@login_required
def copy_budgets():
    body = request.get_json()
    if not isinstance(body, dict):
        raise ValueError('Request body must be an object.')
    source, target = body['source_month'], body['target_month']
    date.fromisoformat(source + '-01')
    date.fromisoformat(target + '-01')
    if source == target:
        return jsonify(error='Choose a different target month.'), 400
    present = {r.data.get('category_id') for r in list_records('budget') if r.data.get('month') == target}
    count = 0
    for row in list_records('budget'):
        if row.data.get('month') == source and row.data.get('category_id') not in present:
            save_record('budget', {**row.data, 'month': target}, g.user)
            count += 1
    db.session.commit()
    return jsonify(copied=count, **budget_summary(target))


def forecast(horizon='month', as_of=None):
    as_of = as_of or today()
    if horizon == 'month':
        start, count = add_months(month_start(as_of), 1), 1
    elif horizon == 'quarter':
        start, count = add_months(date(as_of.year, 3 * ((as_of.month - 1) // 3) + 1, 1), 3), 3
    elif horizon == 'year':
        start, count = date(as_of.year + 1, 1, 1), 12
    else:
        raise ValueError('Horizon must be month, quarter, or year.')
    end = add_months(start, count) - timedelta(days=1)
    rows = transaction_records()
    fulfilled = {(r.get('subscription_id'), r.get('subscription_date')) for r in rows if r.get('subscription_id')}
    categories = {r.id: r.data['name'] for r in list_records('category')}
    groups, due, warnings = {}, [], []
    def group(key):
        return groups.setdefault(key, dict(recurring=ZERO, nonrecurring=ZERO))
    for record in list_records('subscription'):
        sub = record.data
        if sub.get('type', 'Expense') != 'Expense' or not sub.get('active', True):
            continue
        for dt in occurrences(sub, start, end):
            if (record.id, dt.isoformat()) in fulfilled:
                continue
            try:
                amount = dec(sub['amount']) * rate_for(sub.get('currency', 'USD'), as_of.isoformat())
            except (ValueError, LookupError, ApiError) as exc:
                warnings.append(f"{sub.get('name', record.id)}: {exc}")
                continue
            group(sub.get('category_id'))['recurring'] += amount
            due.append(dict(subscription_id=record.id, name=sub.get('name'), date=dt.isoformat(), amount=text(amount), category_id=sub.get('category_id')))
    completed = [add_months(month_start(as_of), -offset).strftime('%Y-%m') for offset in (3, 2, 1)]
    earliest = min((r['date'][:7] for r in rows), default=None)
    coverage = [m for m in completed if earliest and m >= earliest]
    if coverage:
        for row in rows:
            if row['date'][:7] in coverage and not row.get('subscription_id'):
                amount = expense_amount(row)
                if amount:
                    group(row.get('category_id'))['nonrecurring'] += amount / D(len(coverage)) * D(count)
    for amounts in groups.values():
        amounts['nonrecurring'] = max(ZERO, amounts['nonrecurring'])
    recurring = sum((r['recurring'] for r in groups.values()), ZERO)
    nonrecurring = sum((r['nonrecurring'] for r in groups.values()), ZERO)
    items = [dict(category_id=k, name=categories.get(k, 'Uncategorized'), recurring=text(v['recurring']), nonrecurring=text(v['nonrecurring']), total=text(v['recurring'] + v['nonrecurring'])) for k, v in groups.items()]
    return dict(start=start.isoformat(), end=end.isoformat(), recurring=text(recurring), nonrecurring=text(nonrecurring), total=text(recurring + nonrecurring), history_months=coverage, history_available=bool(coverage), items=items, occurrences=sorted(due, key=lambda r: r['date']), warnings=warnings, incomplete=bool(warnings))


@bp.get('/forecast')
@login_required
def get_forecast():
    return jsonify(forecast(request.args.get('horizon', 'month')))


def performance(account_id):
    account = get_record('account', account_id)
    if account.data.get('account_class') != 'asset':
        raise ValueError('Investment performance is available for asset accounts.')
    values = sorted([v.to_dict() for v in list_records('valuation') if v.data.get('account_id') == account_id and v.data['date'] <= today().isoformat()], key=lambda r: r['date'])
    base = dict(account_id=account_id, name=account.data['name'], available=False, intervals=[])
    if len(values) < 2:
        return dict(**base, reason='Enter valuations on at least two different dates.')
    # Use the latest continuous valuation window of up to three years.
    latest = date.fromisoformat(values[-1]['date'])
    floor = add_months(latest, -36)
    values = [v for v in values if date.fromisoformat(v['date']) >= floor]
    if len(values) < 2:
        return dict(**base, reason='At least two valuations within the latest three years are required.')
    rows = transaction_records({'account_id': account_id})
    adjustments = [r.to_dict() for r in list_records('transaction') if r.data.get('system') in ('opening', 'reconciliation') and r.data.get('account_id') == account_id]
    cumulative = D(1)
    intervals = []
    for left, right in zip(values, values[1:]):
        begin, end = date.fromisoformat(left['date']), date.fromisoformat(right['date'])
        days = (end - begin).days
        if days <= 0:
            continue
        flows = weighted = ZERO
        for row in rows:
            when = date.fromisoformat(row['date'])
            if not (begin < when <= end) or row.get('type') == 'Return' or row.get('investment_fee'):
                continue
            incoming = row.get('destination_account_id') == account_id
            flow = (dec(row.get('destination_amount', row['amount'])) * dec(row.get('destination_fx_rate', row.get('fx_rate', '1')))) if incoming else -dec(row.get('usd_amount'))
            flows += flow
            weighted += flow * D((end - when).days) / D(days)
        # A balance correction or newly entered opening principal is not a gain.
        for row in adjustments:
            when = date.fromisoformat(row['date'])
            if begin < when <= end:
                flow = dec(row['usd_amount'])
                flows += flow
                weighted += flow * D((end - when).days) / D(days)
        initial, final = dec(left['usd_amount']), dec(right['usd_amount'])
        denominator = initial + weighted
        if denominator <= 0:
            return dict(**base, reason='Performance is unavailable for an interval with non-positive weighted capital.')
        rate = (final - initial - flows) / denominator
        if rate < -1:
            return dict(**base, reason='Performance is unavailable when an interval loss exceeds its weighted capital.')
        cumulative *= 1 + rate
        intervals.append(dict(start=left['date'], end=right['date'], return_rate=text(rate), external_flows=text(flows)))
    days = (date.fromisoformat(values[-1]['date']) - date.fromisoformat(values[0]['date'])).days
    annual = (cumulative ** (D('365.25') / D(days)) - 1) if days >= 365 else None
    return dict(account_id=account_id, name=account.data['name'], available=bool(intervals), reason=None, start=values[0]['date'], end=values[-1]['date'], days=days, cumulative_return=text(cumulative - 1), annualized_return=text(annual) if annual is not None else None, intervals=intervals, method='Modified Dietz, chained; USD; estimated')


@bp.get('/performance')
@login_required
def get_performance():
    return jsonify(performance(request.args['account_id']))


def project(body):
    if not isinstance(body, dict):
        raise ValueError('Projection request must be an object.')
    current_date = today()
    target = date.fromisoformat(date_string(body.get('date')))
    if target <= current_date or target.year > current_date.year + 100:
        raise ValueError('Choose a future date within 100 years.')
    raw_assumptions, flows = body.get('assumptions', []), body.get('flows', [])
    if not isinstance(raw_assumptions, list) or not all(isinstance(row, dict) for row in raw_assumptions):
        raise ValueError('Projection assumptions must be a list of objects.')
    if not isinstance(flows, list) or not all(isinstance(row, dict) for row in flows):
        raise ValueError('Planned flows must be a list of objects.')
    if len(raw_assumptions) > 1000 or len(flows) > 10000:
        raise ValueError('Projection supports at most 1000 assumptions and 10000 flows.')
    assumptions = {}
    for assumption in raw_assumptions:
        account_id = assumption.get('account_id')
        if not isinstance(account_id, str) or account_id in assumptions:
            raise ValueError('Each projection assumption needs a unique account_id.')
        mode = assumption.get('mode', 'manual')
        if mode not in ('manual', 'historical'):
            raise ValueError('Projection mode must be manual or historical.')
        if mode == 'manual':
            annual_rate = input_decimal(assumption.get('annual_rate', '0'), 'annual_rate')
            if annual_rate <= -1 or annual_rate > 10:
                raise ValueError('Annual return must be greater than -100% and at most 1000%.')
        assumptions[account_id] = assumption
    if any(not isinstance(flow.get('account_id'), str) for flow in flows):
        raise ValueError('Each planned flow needs an account_id.')
    balances = balance_summary(current_date)
    warnings = list(balances['warnings'])
    account_results, settings = [], []
    valid_ids = {r['id'] for r in balances['accounts']}
    if set(assumptions) - valid_ids or any(f.get('account_id') not in valid_ids for f in flows):
        raise ValueError('Projection accounts must be active assets or liabilities.')
    for flow in flows:
        if not current_date < date.fromisoformat(date_string(flow.get('date'), 'flow date')) <= target:
            raise ValueError('Planned flows must fall after today and on or before the target date.')
        input_amount(flow.get('amount'), 'USD', 'flow amount')
    for row in balances['accounts']:
        if row['balance_usd'] is None:
            continue
        current = dec(row['balance_usd']) * (-1 if row['account_class'] == 'liability' else 1)
        assumption = assumptions.get(row['id'], {})
        mode = assumption.get('mode', 'manual')
        rate = ZERO
        if mode == 'historical':
            history = performance(row['id'])
            if not history.get('annualized_return'):
                raise ValueError(f"{row['name']}: historical mode requires at least one year of valid valuations.")
            rate = dec(history['annualized_return'])
        elif mode != 'manual':
            raise ValueError('Projection mode must be manual or historical.')
        else:
            rate = input_decimal(assumption.get('annual_rate', '0'), 'annual_rate')
        if rate <= -1 or rate > 10:
            raise ValueError('Annual return must be greater than -100% and at most 1000%.')
        if not assumption:
            warnings.append(f"{row['name']}: assumes 0% annual return.")
        future = sorted([f for f in flows if f['account_id'] == row['id']], key=lambda f: f['date'])
        settings.append((row, current, rate, mode, future))
    def value_at(current, rate, future, ending):
        value, cursor = current, current_date
        for flow in future:
            day = date.fromisoformat(flow['date'])
            if day > ending:
                break
            value = value * (1 + rate) ** (D((day - cursor).days) / D('365.25')) + dec(flow['amount'])
            cursor = day
        return value * (1 + rate) ** (D((ending - cursor).days) / D('365.25'))
    total = ZERO
    for row, current, rate, mode, future in settings:
        value = value_at(current, rate, future, target)
        total += value
        account_results.append(dict(account_id=row['id'], name=row['name'], current_value=text(current), projected_value=text(value), annual_rate=text(rate), mode=mode))
    checkpoints = [current_date]
    cursor = add_months(month_start(current_date), 1)
    while cursor < target:
        checkpoints.append(cursor)
        cursor = add_months(cursor, max(1, (target.year - current_date.year) // 10))
    checkpoints.append(target)
    series = [dict(date=point.isoformat(), total=text(sum((value_at(current, rate, future, point) for _, current, rate, _, future in settings), ZERO))) for point in checkpoints]
    return dict(date=target.isoformat(), total=text(total), accounts=account_results, series=series, warnings=warnings, incomplete=balances['incomplete'], currency='USD', flow_currency='USD', assumption='Annual USD total return; planned flows are USD and applied at end of day.')


@bp.post('/projections')
@login_required
def projections():
    return jsonify(project(request.get_json()))
