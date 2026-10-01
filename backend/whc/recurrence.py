"""Date-only recurrence anchored to the original day, including month-end."""
import calendar
from datetime import date, timedelta
from .validation import ApiError


def occurrences(subscription_data, start, end):
    anchor = date.fromisoformat(subscription_data["start_date"])
    start = date.fromisoformat(start) if isinstance(start, str) else start
    end = date.fromisoformat(end) if isinstance(end, str) else end
    if subscription_data.get("end_date"):
        end = min(end, date.fromisoformat(subscription_data["end_date"]))
    if end < start or not subscription_data.get("active", True):
        return []
    frequency = subscription_data.get("frequency", "monthly")
    interval = int(subscription_data.get("interval", 1))
    result = []
    if frequency in ("daily", "weekly"):
        step = interval * (7 if frequency == "weekly" else 1)
        n = max(0, (start - anchor).days // step)
        current = anchor + timedelta(days=n * step)
        while current <= end:
            if current >= start:
                result.append(current)
            current += timedelta(days=step)
    else:
        step = interval * (12 if frequency == "yearly" else 1)
        month_distance = (start.year - anchor.year) * 12 + start.month - anchor.month
        n = max(0, month_distance // step)
        while True:
            total = anchor.year * 12 + anchor.month - 1 + n * step
            year, month = divmod(total, 12)
            if year > 9999:
                break
            month += 1
            current = date(year, month, min(anchor.day, calendar.monthrange(year, month)[1]))
            if current > end:
                break
            if current >= start:
                result.append(current)
            n += 1
    if len(result) > 10000:
        raise ApiError("The recurrence range is too large")
    return result
