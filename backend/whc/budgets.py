"""Planning credits are derived values and never create ledger postings."""
import calendar
from datetime import date
from decimal import Decimal


def credit_count(data, through):
    """Count anchored credits without expanding years of daily occurrences."""
    anchor = date.fromisoformat(data['start_date'])
    end = min(through, date.fromisoformat(data['end_date'])) if data.get('end_date') else through
    if end < anchor:
        return 0
    period, frequency = data['period'], data['frequency']
    if period in ('day', 'week'):
        return (end - anchor).days // (frequency * (7 if period == 'week' else 1)) + 1
    step = frequency * (12 if period == 'year' else 1)
    months = (end.year - anchor.year) * 12 + end.month - anchor.month
    n = months // step
    index = anchor.year * 12 + anchor.month - 1 + n * step
    year, month = divmod(index, 12)
    last = date(year, month + 1, min(anchor.day, calendar.monthrange(year, month + 1)[1]))
    return n + (last <= end)


def credited(data, through):
    return Decimal(data['amount']) * credit_count(data, through)
