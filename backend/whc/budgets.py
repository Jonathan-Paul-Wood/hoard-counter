"""Planning credits are derived values and never create ledger postings."""
import calendar
from datetime import date
from decimal import Decimal


def normalize_budget(data):
    data = dict(data)
    if data.get('month'):
        start = date.fromisoformat(data.pop('month') + '-01')
        data.update(start_date=start.isoformat(), end_date=start.replace(
            day=calendar.monthrange(start.year, start.month)[1]).isoformat(), frequency='monthly')
    return data


def credit_count(data, through):
    """Count anchored credits without expanding years of daily occurrences."""
    data = normalize_budget(data)
    anchor = date.fromisoformat(data['start_date'])
    end = min(through, date.fromisoformat(data['end_date'])) if data.get('end_date') else through
    if end < anchor:
        return 0
    frequency = data['frequency']
    if frequency in ('daily', 'weekly'):
        return (end - anchor).days // (7 if frequency == 'weekly' else 1) + 1
    step = {'monthly': 1, 'quarterly': 3, 'annually': 12}[frequency]
    months = (end.year - anchor.year) * 12 + end.month - anchor.month
    n = months // step
    index = anchor.year * 12 + anchor.month - 1 + n * step
    year, month = divmod(index, 12)
    last = date(year, month + 1, min(anchor.day, calendar.monthrange(year, month + 1)[1]))
    return n + (last <= end)


def credited(data, through):
    return Decimal(data['amount']) * credit_count(data, through)
