from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from uuid import UUID
import re


class ApiError(Exception):
    def __init__(self, message, status=400, code="validation_error", details=None):
        self.message, self.status, self.code, self.details = message, status, code, details
        super().__init__(message)


CURRENCIES = {
    "USD": ("US Dollar", 2), "EUR": ("Euro", 2), "GBP": ("British Pound", 2),
    "JPY": ("Japanese Yen", 0), "BTC": ("Bitcoin", 8), "ETH": ("Ether", 18),
    "CAD": ("Canadian Dollar", 2), "AUD": ("Australian Dollar", 2),
    "CHF": ("Swiss Franc", 2), "CNY": ("Chinese Yuan", 2), "HKD": ("Hong Kong Dollar", 2),
    "NZD": ("New Zealand Dollar", 2), "SEK": ("Swedish Krona", 2),
    "NOK": ("Norwegian Krone", 2), "DKK": ("Danish Krone", 2),
    "SGD": ("Singapore Dollar", 2), "INR": ("Indian Rupee", 2),
    "KRW": ("South Korean Won", 0), "MXN": ("Mexican Peso", 2),
    "BRL": ("Brazilian Real", 2), "ZAR": ("South African Rand", 2),
}
ASSET_TYPES = ["Checking", "Savings", "Brokerage", "Roth IRA", "Traditional IRA", "Roth 401K", "Traditional 401K", "HSA", "FSA", "529", "Wallet", "Crypto Cold Wallet"]
LIABILITY_TYPES = ["Credit Card", "Loan", "Mortgage", "Debt"]
ACCOUNT_TYPES = ASSET_TYPES + LIABILITY_TYPES + ["Income Source", "Expense Payee", "Equity"]
TRANSACTION_TYPES = ["Income", "Expense", "Transfer", "Return"]


def decimal(value, field="amount"):
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise ApiError(f"{field} must be an exact decimal string")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ApiError(f"{field} must be a valid decimal")
    if not result.is_finite() or abs(result) >= Decimal("1e30") or result.as_tuple().exponent < -24:
        raise ApiError(f"{field} must be finite, below 1e30, and have at most 24 decimal places")
    return result


def money(value):
    result = value if isinstance(value, Decimal) else decimal(value)
    return format(result, "f") if result else "0"


def quantize(value, currency="USD"):
    with localcontext() as context:
        context.prec = 100
        return value.quantize(Decimal(1).scaleb(-currency_definition(currency)[1]), rounding=ROUND_HALF_EVEN)


def currency_definition(code):
    if code in CURRENCIES:
        return CURRENCIES[code]
    from flask import has_app_context
    if has_app_context():
        from .models import db, Record
        for record in db.session.scalars(db.select(Record).where(Record.kind == "currency", Record.deleted.is_(False))):
            if record.data.get("code") == code:
                return record.data["name"], record.data["precision"]
    raise ApiError("Unsupported currency", details={"currency": code})


def currency_definitions():
    from .models import db, Record
    result = [{"id": code, "code": code, "name": name, "precision": precision, "builtin": True}
              for code, (name, precision) in CURRENCIES.items()]
    for record in db.session.scalars(db.select(Record).where(Record.kind == "currency", Record.deleted.is_(False))):
        result.append({**record.to_dict(), "builtin": False})
    return result


def currency_code(value):
    code = str(value or "USD").upper()
    currency_definition(code)
    return code


def amount(value, currency="USD", field="amount", positive=False):
    result = decimal(value, field)
    if positive and result <= 0:
        raise ApiError(f"{field} must be greater than zero")
    if quantize(result, currency) != result:
        raise ApiError(f"{field} supports at most {currency_definition(currency)[1]} decimal places for {currency}")
    return money(result)


def date_string(value, field="date"):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ApiError(f"{field} must be a valid YYYY-MM-DD date")
    return value


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (AttributeError, TypeError, ValueError):
        raise ApiError("last_modified must be an ISO 8601 timestamp with timezone")


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ApiError("id must be a UUID")


def required_text(data, key, max_length=300):
    text = data.get(key)
    if not isinstance(text, str) or not text.strip() or len(text) > max_length:
        raise ApiError(f"{key} is required and must contain at most {max_length} characters")
    data[key] = text.strip()
    return data[key]
