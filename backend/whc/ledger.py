"""Single mutation path for the household ledger, imports, rules, and valuations.

The caller owns commit/rollback. All balances derive from balanced postings.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, DefaultContext, getcontext
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo
import re

from flask import current_app
from sqlalchemy import select
from .models import db, Record, Posting, AuditEvent, utcnow, new_id
from .validation import (ApiError, ACCOUNT_TYPES, ASSET_TYPES, LIABILITY_TYPES, INVESTMENT_TYPES,
                         TRANSACTION_TYPES, amount, currency_code, date_string,
                         decimal, identifier, money, quantize, required_text, timestamp, CURRENCIES)
from .recurrence import occurrences

getcontext().prec = 100
DefaultContext.prec = 100
KINDS = {"account", "transaction", "category", "tag", "subscription", "rule", "budget", "rate", "valuation", "scenario", "currency"}
META_FIELDS = {"id", "revision", "last_modified", "deleted", "balance", "balance_usd", "signed_balance", "signed_balance_usd", "postings", "as_of", "valuation_date", "missing_rate"}
SYSTEM_ACCOUNTS = {
    "opening": ("Opening balances", "equity", "Equity"),
    "valuation": ("Market valuation gains and losses", "equity", "Equity"),
    "fx": ("Currency conversion gains and losses", "equity", "Equity"),
    "reconciliation": ("Balance reconciliation", "equity", "Equity"),
    "income": ("Income", "income", "Income Source"),
    "expense": ("Expenses", "expense", "Expense Payee"),
}


def system_id(name):
    return str(uuid5(NAMESPACE_URL, "hoard-counter/system/" + name))


def seed_system_accounts():
    for key, (name, account_class, account_type) in SYSTEM_ACCOUNTS.items():
        rid = system_id(key)
        if db.session.get(Record, rid) is None:
            db.session.add(Record(id=rid, kind="account", data={"name": name, "type": account_type,
                "account_class": account_class, "currency": "USD", "system": key,
                "opening_balance": "0", "opening_date": "1970-01-01"}))
    db.session.flush()


def get_record(kind, record_id, include_deleted=False):
    record = db.session.get(Record, str(record_id))
    if record is None or record.kind != kind or (record.deleted and not include_deleted):
        raise ApiError(f"{kind.capitalize()} not found", 404, "not_found")
    return record


def list_records(kind, include_deleted=False):
    statement = select(Record).where(Record.kind == kind)
    if not include_deleted:
        statement = statement.where(Record.deleted.is_(False))
    return list(db.session.scalars(statement.order_by(Record.last_modified.desc(), Record.id)))


def add_audit(kind, record_id, action, actor, origin, before, after):
    actor_id = actor.id if hasattr(actor, "id") else actor
    db.session.add(AuditEvent(kind=kind, record_id=record_id, action=action, actor_id=actor_id,
                             origin=origin, before=deepcopy(before), after=deepcopy(after)))


def rate_for(currency, when):
    currency = currency_code(currency)
    if currency == "USD":
        return Decimal(1)
    when = when.isoformat() if isinstance(when, date) else date_string(when)
    candidates = [r for r in list_records("rate") if r.data["currency"] == currency and r.data["date"] <= when]
    if not candidates:
        raise ApiError(f"Enter a {currency}/USD conversion rate on or before {when}", 422, "missing_rate", {"currency": currency, "date": when})
    return decimal(max(candidates, key=lambda r: r.data["date"]).data["rate"], "rate")


def account_balance(account_id, through=None):
    query = select(Posting, Record).join(Record, Posting.transaction_id == Record.id).where(
        Posting.account_id == account_id, Record.deleted.is_(False))
    total = Decimal(0)
    through = through.isoformat() if isinstance(through, date) else through
    for posting, record in db.session.execute(query):
        if through is None or record.data.get("date", "") <= through:
            total += decimal(posting.native_amount)
    return total


def public_record(record):
    result = record.to_dict()
    if record.kind == "account":
        as_of = datetime.now(ZoneInfo(current_app.config.get("HOUSEHOLD_TIMEZONE", "America/New_York"))).date().isoformat()
        balance = account_balance(record.id, through=as_of)
        sign = -1 if record.data.get("account_class") == "liability" else 1
        valuation_date = max((valuation.data["date"] for valuation in list_records("valuation")
                              if valuation.data.get("account_id") == record.id and valuation.data["date"] <= as_of), default=None)
        result.update(signed_balance=money(balance), balance=money(sign * balance),
                      as_of=as_of, valuation_date=valuation_date, missing_rate=False)
        try:
            usd = balance * rate_for(record.data["currency"], as_of) if balance else Decimal(0)
            result.update(signed_balance_usd=money(usd), balance_usd=money(sign * usd))
        except ApiError as error:
            if error.code != "missing_rate":
                raise
            result.update(signed_balance_usd=None, balance_usd=None, missing_rate=True)
    return result


def _reference(data, field, kind, required=False, include_deleted=False):
    value = data.get(field)
    if value:
        return get_record(kind, value, include_deleted)
    if required:
        raise ApiError(f"{field} is required")
    data[field] = None
    return None


def account_history(account_id):
    return [r for r in list_records("transaction", True)
            if account_id in (r.data.get("account_id"), r.data.get("source_account_id"), r.data.get("destination_account_id"))]


def has_ledger_history(history):
    return any(r.data.get("system") != "opening" or decimal(r.data.get("amount", "0")) != 0 for r in history)


def _validate_account(data, existing, preserve_snapshots=False):
    required_text(data, "name", 160)
    number = data.get("account_number")
    if number is not None and (not isinstance(number, str) or len(number) > 160):
        raise ApiError("account_number must be text with at most 160 characters")
    data["account_number"] = (number.strip() or None) if number is not None else None
    if data.get("type") not in ACCOUNT_TYPES:
        raise ApiError("Choose a supported account type")
    inferred = "asset" if data["type"] in ASSET_TYPES else "liability" if data["type"] in LIABILITY_TYPES else {"Income Source": "income", "Expense Payee": "expense", "Equity": "equity"}[data["type"]]
    if existing and data["type"] != existing.data.get("type") and data.get("account_class") == existing.data.get("account_class"):
        data["account_class"] = inferred
    else:
        data["account_class"] = data.get("account_class", inferred)
    if data["account_class"] != inferred:
        raise ApiError("Account type and accounting class do not agree")
    data["currency"] = currency_code(data.get("currency", "USD"))
    data["opening_currency"] = currency_code(data.get("opening_currency", existing.data["currency"] if existing else data["currency"]))
    data["opening_balance"] = amount(data.get("opening_balance", "0"), data["opening_currency"], "opening_balance")
    if inferred == "liability" and decimal(data["opening_balance"]) > 0:
        data["opening_balance"] = money(-decimal(data["opening_balance"]))
    data["opening_date"] = date_string(data.get("opening_date", date.today().isoformat()), "opening_date")
    changed = not existing or data["opening_date"] != existing.data.get("opening_date") or data["opening_currency"] != existing.data.get("opening_currency", existing.data["currency"]) or (existing and not decimal(existing.data.get("opening_balance", "0")) and decimal(data["opening_balance"]))
    if changed and not preserve_snapshots:
        data.pop("opening_posting_snapshots", None)
    if (changed and not (preserve_snapshots and data.get("opening_fx_rate"))) or not data.get("opening_fx_rate"):
        data["opening_fx_rate"] = money(rate_for(data["opening_currency"], data["opening_date"])) if decimal(data["opening_balance"]) else "1"
    if decimal(data["opening_fx_rate"], "opening_fx_rate") <= 0:
        raise ApiError("Opening conversion rate must be positive")
    if existing and existing.data.get("system"):
        raise ApiError("Built-in counterpart accounts cannot be edited", 409)
    if existing and data["account_class"] != existing.data.get("account_class"):
        if has_ledger_history(account_history(existing.id)):
            raise ApiError("An account's accounting class cannot change after it has postings", 409)


def _validate_transaction(data, existing=None, preserve_snapshots=False, historical=False):
    source = _reference(data, "source_account_id", "account", True, historical)
    destination = _reference(data, "destination_account_id", "account", True, historical)
    is_return = data.get("type") == "Return"
    if is_return:
        return_type = source.data.get("type")
        if existing and existing.data.get("type") == "Return" and existing.data.get("source_account_id") == source.id:
            return_type = existing.data.get("return_account_type", return_type)
        elif preserve_snapshots:
            return_type = data.get("return_account_type", return_type)
        if source.id != destination.id or return_type not in INVESTMENT_TYPES or source.data.get("account_class") != "asset":
            raise ApiError("Returns require the same investment account as source and destination")
    elif source.id == destination.id:
        raise ApiError("Source and destination must be different accounts")
    if data.get("type") not in TRANSACTION_TYPES:
        raise ApiError("Choose Income, Expense, Transfer, or Return")
    required_text(data, "description", 1000)
    data["date"] = date_string(data.get("date"))
    sclass, dclass = source.data["account_class"], destination.data["account_class"]
    financial = {"asset", "liability"}
    valid = {"Income": sclass == "income" and dclass in financial,
             "Return": is_return,
             "Transfer": sclass in financial and dclass in financial,
             "Expense": (sclass in financial and dclass == "expense") or (bool(data.get("is_refund")) and sclass == "expense" and dclass in financial)}
    if not valid[data["type"]]:
        raise ApiError("Transaction type does not match its source and destination account classes")
    if data.get("is_refund") and data["type"] != "Expense":
        raise ApiError("Only expenses may be marked as refunds")
    if data.get("investment_fee") and (data["type"] != "Expense" or data.get("is_refund")):
        raise ApiError("Investment fees must be outgoing expenses")
    data["currency"] = currency_code(data.get("currency") or source.data["currency"])
    data["destination_currency"] = currency_code(data.get("destination_currency") or (data["currency"] if is_return else destination.data["currency"]))
    if is_return and data["destination_currency"] != data["currency"]:
        raise ApiError("Return currencies must agree")
    data["amount"] = amount(data.get("amount"), data["currency"], positive=not is_return)
    if is_return and decimal(data["amount"]) == 0:
        raise ApiError("Return amount must be positive or negative, not zero")
    # Preserve the exact saved conversion when an unrelated field is edited.
    s_changed = not existing or any(data.get(k) != existing.data.get(k) for k in ("date", "source_account_id", "currency"))
    d_changed = not existing or any(data.get(k) != existing.data.get(k) for k in ("date", "destination_account_id", "destination_currency"))
    if existing and data.get("date") != existing.data.get("date") and not preserve_snapshots:
        data.pop("posting_snapshots", None)
    if (s_changed and not (preserve_snapshots and data.get("fx_rate"))) or "fx_rate" not in data:
        data["fx_rate"] = money(rate_for(data["currency"], data["date"]))
    if (d_changed and not (preserve_snapshots and data.get("destination_fx_rate"))) or "destination_fx_rate" not in data:
        data["destination_fx_rate"] = money(rate_for(data["destination_currency"], data["date"]))
    if decimal(data["fx_rate"], "fx_rate") <= 0 or decimal(data["destination_fx_rate"], "destination_fx_rate") <= 0:
        raise ApiError("Conversion rates must be positive")
    if is_return:
        # Both fields represent one account; use the same saved conversion.
        data["destination_fx_rate"] = data["fx_rate"]
    data["usd_amount"] = money(decimal(data["amount"]) * decimal(data["fx_rate"]))
    if not data.get("destination_amount"):
        data["destination_amount"] = money(quantize(decimal(data["usd_amount"]) / decimal(data["destination_fx_rate"]), data["destination_currency"]))
    data["destination_amount"] = amount(data["destination_amount"], data["destination_currency"], "destination_amount", positive=not is_return)
    data["destination_usd_amount"] = money(decimal(data["destination_amount"]) * decimal(data["destination_fx_rate"]))
    if data["currency"] == data["destination_currency"] and decimal(data["amount"]) != decimal(data["destination_amount"]):
        raise ApiError("Amounts must agree for a transfer in the same currency; record fees separately")
    _reference(data, "category_id", "category", include_deleted=historical)
    tag_ids = data.get("tag_ids", [])
    if not isinstance(tag_ids, list) or len(tag_ids) > 100:
        raise ApiError("tag_ids must be a list with at most 100 tags")
    data["tag_ids"] = list(dict.fromkeys(tag_ids))
    for tag_id in data["tag_ids"]:
        get_record("tag", tag_id, historical)
    if data.get("subscription_id"):
        get_record("subscription", data["subscription_id"], historical)
    if data.get("subscription_date"):
        data["subscription_date"] = date_string(data["subscription_date"], "subscription_date")
    return source, destination


def _validate(kind, data, existing, preserve_snapshots=False, historical=False):
    if kind == "account":
        _validate_account(data, existing, preserve_snapshots)
    elif kind == "transaction":
        _validate_transaction(data, existing, preserve_snapshots, historical)
    elif kind in ("category", "tag"):
        required_text(data, "name", 120)
    elif kind == "currency":
        required_text(data, "name", 120)
        data["code"] = str(data.get("code", "")).upper()
        if not re.fullmatch(r"[A-Z]{3}", data["code"]) or data["code"] in CURRENCIES:
            raise ApiError("Use an unused three-letter currency code; built-in currencies are read-only")
        data["precision"] = _integer(data.get("precision", 2), 0, 18, "precision")
        if not historical and any(r.id != (existing.id if existing else None) and r.data.get("code") == data["code"] for r in list_records("currency")):
            raise ApiError("A definition already exists for this currency code", 409)
        if existing and any(data.get(k) != existing.data.get(k) for k in ("code", "precision")) and _currency_referenced(existing.data["code"]):
            raise ApiError("A currency's code and precision cannot change while it is in use", 409)
    elif kind == "rate":
        data["currency"] = currency_code(data.get("currency"))
        data["date"] = date_string(data.get("date"))
        data["rate"] = money(decimal(data.get("rate"), "rate"))
        if decimal(data["rate"]) <= 0 or (data["currency"] == "USD" and decimal(data["rate"]) != 1):
            raise ApiError("Rate must be positive; the USD rate must equal 1")
        if not historical and any(r.id != (existing.id if existing else None) and r.data["currency"] == data["currency"] and r.data["date"] == data["date"] for r in list_records("rate")):
            raise ApiError("A rate already exists for this currency and date", 409)
    elif kind == "budget":
        month = data.get("month", "")
        date_string(month + "-01", "month")
        _reference(data, "category_id", "category", True, historical)
        data["amount"] = amount(data.get("amount"), "USD")
        if decimal(data["amount"]) < 0:
            raise ApiError("Budget amount cannot be negative")
        if not historical and any(r.id != (existing.id if existing else None) and r.data.get("month") == month and r.data.get("category_id") == data["category_id"] for r in list_records("budget")):
            raise ApiError("This category already has a budget for that month", 409)
    elif kind == "subscription":
        required_text(data, "name", 160)
        source = _reference(data, "source_account_id", "account", True, historical)
        _reference(data, "destination_account_id", "account", True, historical)
        is_return = data.get("type") == "Return"
        if is_return:
            return_type = source.data.get("type")
            if existing and existing.data.get("type") == "Return" and existing.data.get("source_account_id") == source.id:
                return_type = existing.data.get("return_account_type", return_type)
            elif preserve_snapshots:
                return_type = data.get("return_account_type", return_type)
            if data["source_account_id"] != data["destination_account_id"] or return_type not in INVESTMENT_TYPES or source.data.get("account_class") != "asset":
                raise ApiError("Returns require the same investment account as source and destination")
        elif data["source_account_id"] == data["destination_account_id"]:
            raise ApiError("Subscription accounts must differ")
        data["currency"] = currency_code(data.get("currency", source.data["currency"]))
        data["amount"] = amount(data.get("amount"), data["currency"], positive=not is_return)
        if is_return and decimal(data["amount"]) == 0:
            raise ApiError("Return amount must be positive or negative, not zero")
        data["amount_tolerance"] = amount(data.get("amount_tolerance", "0"), data["currency"], "amount_tolerance")
        if decimal(data["amount_tolerance"]) < 0:
            raise ApiError("Amount tolerance cannot be negative")
        data["day_tolerance"] = _integer(data.get("day_tolerance", 3), 0, 31, "day_tolerance")
        data["interval"] = _integer(data.get("interval", 1), 1, 120, "interval")
        data["frequency"] = data.get("frequency", "monthly")
        if data["frequency"] not in ("daily", "weekly", "monthly", "yearly"):
            raise ApiError("Unsupported recurrence frequency")
        data["type"] = data.get("type", "Expense")
        if data["type"] not in TRANSACTION_TYPES:
            raise ApiError("Unsupported subscription transaction type")
        data["start_date"] = date_string(data.get("start_date"), "start_date")
        if data.get("end_date"):
            data["end_date"] = date_string(data["end_date"], "end_date")
            if data["end_date"] < data["start_date"]:
                raise ApiError("Subscription end precedes its start")
        _reference(data, "category_id", "category", include_deleted=historical)
        data["active"] = bool(data.get("active", True))
    elif kind == "valuation":
        account = _reference(data, "account_id", "account", True, historical)
        if account.data["account_class"] != "asset":
            raise ApiError("Market valuations apply to asset accounts")
        data["date"] = date_string(data.get("date"))
        data["currency"] = currency_code(data.get("currency") or account.data["currency"])
        data["amount"] = amount(data.get("amount"), data["currency"])
        if decimal(data["amount"]) < 0:
            raise ApiError("Market valuation cannot be negative")
        if not historical and any(r.id != (existing.id if existing else None) and r.data["account_id"] == account.id and r.data["date"] == data["date"] for r in list_records("valuation")):
            raise ApiError("An account already has a valuation for this date", 409)
        changed = not existing or any(data.get(k) != existing.data.get(k) for k in ("account_id", "date", "currency"))
        if changed and not preserve_snapshots:
            data.pop("posting_snapshots", None)
        if (changed and not (preserve_snapshots and data.get("fx_rate"))) or not data.get("fx_rate"):
            data["fx_rate"] = money(rate_for(data["currency"], data["date"]))
        data["usd_amount"] = money(decimal(data["amount"]) * decimal(data["fx_rate"]))
    elif kind == "rule":
        required_text(data, "name", 160)
        data["priority"] = _integer(data.get("priority", 100), 0, 100000, "priority")
        data["enabled"] = bool(data.get("enabled", True))
        data["match"] = data.get("match", "all")
        if data["match"] not in ("all", "any"):
            raise ApiError("Rule match must be all or any")
        conditions = data.get("conditions", [])
        if not isinstance(conditions, list) or not conditions or len(conditions) > 30:
            raise ApiError("Rules need between 1 and 30 conditions")
        for condition in conditions:
            if not isinstance(condition, dict) or condition.get("field") not in {"amount", "description", "source_account_id", "destination_account_id", "type", "category_id", "tag_ids", "notes", "currency"} or condition.get("operator") not in {"eq", "neq", "contains", "gte", "lte"}:
                raise ApiError("Unsupported rule condition")
            if condition["field"] == "tag_ids":
                if condition["operator"] != "contains":
                    raise ApiError("Tag conditions use contains with a tag UUID")
                get_record("tag", condition.get("value"), historical)
            if condition["operator"] in ("gte", "lte") or condition["field"] == "amount":
                decimal(condition.get("value"), "condition value")
        actions = data.get("actions", {})
        if not isinstance(actions, dict) or not actions or set(actions) - {"category_id", "tag_ids", "notes", "subscription_id"}:
            raise ApiError("Rule actions can set category, tags, notes, or subscription")
        for field, refkind in (("category_id", "category"), ("subscription_id", "subscription")):
            if actions.get(field):
                get_record(refkind, actions[field], historical)
        if "tag_ids" in actions:
            if not isinstance(actions["tag_ids"], list):
                raise ApiError("Rule tag_ids must be a list")
            for tag_id in actions["tag_ids"]:
                get_record("tag", tag_id, historical)
    elif kind == "scenario":
        required_text(data, "name", 160)
        if data.get("target_date"):
            date_string(data["target_date"], "target_date")
    for field in ("notes",):
        if field in data and data[field] is not None and (not isinstance(data[field], str) or len(data[field]) > 10000):
            raise ApiError(f"{field} must contain at most 10000 characters")


def _integer(value, minimum, maximum, field):
    try:
        parsed = int(value)
        if isinstance(value, bool) or str(parsed) != str(value) or not minimum <= parsed <= maximum:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise ApiError(f"{field} must be an integer between {minimum} and {maximum}")


def _currency_referenced(code):
    return any(code in (r.data.get("currency"), r.data.get("destination_currency"), r.data.get("opening_currency"))
               for kind in ("account", "transaction", "valuation", "subscription", "rate") for r in list_records(kind, True))


def rule_matches(rule, data):
    values = []
    for condition in rule.get("conditions", []):
        actual, expected = data.get(condition["field"]), condition.get("value")
        operator = condition["operator"]
        if condition["field"] == "tag_ids":
            values.append(expected in (actual or []))
            continue
        if condition["field"] == "amount" or operator in ("gte", "lte"):
            try:
                actual, expected = decimal(actual), decimal(expected)
            except ApiError:
                values.append(False)
                continue
        values.append({"eq": lambda: actual == expected, "neq": lambda: actual != expected,
                       "contains": lambda: str(expected).casefold() in str(actual or "").casefold(),
                       "gte": lambda: actual >= expected, "lte": lambda: actual <= expected}[operator]())
    return bool(values) and (any(values) if rule.get("match") == "any" else all(values))


def apply_transaction_rules(data, rules=None):
    result = deepcopy(data)
    applied = []
    for rule in sorted(rules if rules is not None else list_records("rule"), key=lambda r: (r.data.get("priority", 100), r.id)):
        if rule.data.get("enabled", True) and rule_matches(rule.data, result):
            for key, value in rule.data["actions"].items():
                # A deliberately selected subscription has precedence over automation.
                if key == "subscription_id" and result.get("subscription_id"):
                    continue
                if key == "tag_ids":
                    result[key] = list(dict.fromkeys(result.get(key, []) + value))
                else:
                    result[key] = deepcopy(value)
            applied.append(rule.id)
    result["applied_rule_ids"] = applied
    return result


def _link_subscription(data, transaction_id):
    explicit = data.get("subscription_id")
    subs = [get_record("subscription", explicit)] if explicit else list_records("subscription")
    txdate = date.fromisoformat(data["date"])
    used = {(t.data.get("subscription_id"), t.data.get("subscription_date")) for t in list_records("transaction") if t.id != transaction_id and t.data.get("subscription_id")}
    candidates = []
    for subscription in subs:
        sub = subscription.data
        if not explicit and (not sub.get("active", True) or any(sub.get(k) != data.get(k) for k in ("source_account_id", "destination_account_id", "type", "currency")) or abs(decimal(sub["amount"]) - decimal(data["amount"])) > decimal(sub.get("amount_tolerance", "0"))):
            continue
        tolerance = int(sub.get("day_tolerance", 3))
        for due in occurrences(sub, txdate - timedelta(days=tolerance), txdate + timedelta(days=tolerance)):
            occurrence = (subscription.id, due.isoformat())
            if data.get("subscription_date") and explicit and due.isoformat() != data["subscription_date"]:
                continue
            if occurrence not in used:
                candidates.append(occurrence)
    if len(candidates) == 1:
        data["subscription_id"], data["subscription_date"] = candidates[0]
        data.pop("subscription_candidates", None)
    elif explicit:
        raise ApiError("Choose an unfulfilled subscription occurrence within its date window", 409, "subscription_conflict")
    elif candidates:
        data["subscription_candidates"] = [{"subscription_id": sid, "date": when} for sid, when in candidates]


def _posting_snapshots(data):
    snapshots = data.get("posting_snapshots", {})
    if not isinstance(snapshots, dict) or len(snapshots) > 1000:
        raise ApiError("posting_snapshots must be an object with at most 1000 accounts")
    for snapshot in snapshots.values():
        if not isinstance(snapshot, dict) or not snapshot.get("currency"):
            raise ApiError("Each posting snapshot needs a currency and positive fx_rate")
        currency_code(snapshot["currency"])
        if decimal(snapshot.get("fx_rate"), "posting conversion rate") <= 0:
            raise ApiError("Posting conversion rates must be positive")
    return deepcopy(snapshots)


def _replace_postings(record, entries):
    if sum((value[2] for value in entries), Decimal(0)) != 0:
        raise ApiError("Journal postings do not balance", 500, "unbalanced_journal")
    db.session.query(Posting).filter_by(transaction_id=record.id).delete(synchronize_session=False)
    snapshots = _posting_snapshots(record.data)
    for account_id, native, usd in entries:
        account = get_record("account", account_id, True)
        currency = account.data["currency"]
        data = record.data
        entry_currency = (data.get("currency", currency) if account_id == data.get("account_id") or account_id == data.get("source_account_id")
                          else data.get("destination_currency", currency) if account_id == data.get("destination_account_id") else "USD")
        if entry_currency != currency and native:
            snapshot = snapshots.get(account_id, {})
            if snapshot.get("currency") != currency:
                snapshot = {"currency": currency, "fx_rate": money(rate_for(currency, data["date"]))}
                snapshots[account_id] = snapshot
            conversion = decimal(snapshot["fx_rate"], "posting conversion rate")
            if conversion <= 0:
                raise ApiError("Posting conversion rates must be positive")
            native = quantize(usd / conversion, currency)
        db.session.add(Posting(transaction_id=record.id, account_id=account_id,
                               native_amount=money(native), usd_amount=money(usd)))
    if snapshots:
        record.data = {**record.data, "posting_snapshots": snapshots}
    db.session.flush()


def _transaction_postings(record):
    data = record.data
    source_usd, destination_usd = decimal(data["usd_amount"]), decimal(data["destination_usd_amount"])
    if data["type"] == "Return":
        _replace_postings(record, [
            (data["source_account_id"], decimal(data["amount"]), source_usd),
            (system_id("income"), -source_usd, -source_usd),
        ])
        return
    entries = [(data["source_account_id"], -decimal(data["amount"]), -source_usd),
               (data["destination_account_id"], decimal(data["destination_amount"]), destination_usd)]
    difference = source_usd - destination_usd
    if difference:
        entries.append((system_id("fx"), difference, difference))
    _replace_postings(record, entries)


def _system_journal(key, system, account_id, native, when, rate, actor=None, extra=None, force_postings=False):
    rid = system_id("journal/" + key)
    record = db.session.get(Record, rid)
    before = record.to_dict() if record else None
    data = {"system": system, "account_id": account_id, "date": when,
            "currency": get_record("account", account_id, True).data["currency"],
            "description": {"opening": "Opening balance", "valuation": "Market valuation adjustment", "reconciliation": "Balance reconciliation"}[system],
            "amount": money(native), "usd_amount": money(native * rate), "fx_rate": money(rate), **(extra or {})}
    if not record:
        record = Record(id=rid, kind="transaction", data=data)
        db.session.add(record)
    elif {k: v for k, v in record.data.items() if k != "posting_snapshots"} == {k: v for k, v in data.items() if k != "posting_snapshots"} and not record.deleted:
        if force_postings:
            usd = native * rate
            _replace_postings(record, [(account_id, native, usd), (system_id(system), -usd, -usd)])
        return record
    else:
        if record.data.get("date") == when and record.data.get("posting_snapshots"):
            data["posting_snapshots"] = record.data["posting_snapshots"]
        record.data = data
        record.deleted = False
        record.revision += 1
        record.last_modified = utcnow()
    db.session.flush()
    usd = native * rate
    _replace_postings(record, [(account_id, native, usd), (system_id(system), -usd, -usd)])
    add_audit("transaction", rid, "create" if before is None else "update", actor, "derived", before, record.to_dict())
    return record


def rebuild_account_valuations(account_id, actor=None):
    valuations = sorted([v for v in list_records("valuation") if v.data["account_id"] == account_id], key=lambda v: v.data["date"])
    journals = [t for t in list_records("transaction", True) if t.data.get("system") == "valuation" and t.data.get("account_id") == account_id]
    # Temporarily remove all generated marks; each absolute target is replayed once.
    for journal in journals:
        db.session.query(Posting).filter_by(transaction_id=journal.id).delete(synchronize_session=False)
    db.session.flush()
    active_ids = set()
    for valuation in valuations:
        target = decimal(valuation.data["amount"])
        currency = get_record("account", account_id, True).data["currency"]
        valuation_currency = valuation.data.get("currency", currency)
        conversion = decimal(valuation.data["fx_rate"])
        if valuation_currency != currency:
            snapshots = _posting_snapshots(valuation.data)
            snapshot = snapshots.get(account_id, {})
            if snapshot.get("currency") != currency:
                snapshot = {"currency": currency, "fx_rate": money(rate_for(currency, valuation.data["date"]))}
                snapshots[account_id] = snapshot
                valuation.data = {**valuation.data, "posting_snapshots": snapshots}
            conversion = decimal(snapshot["fx_rate"])
            if conversion <= 0:
                raise ApiError("Posting conversion rates must be positive")
            target = quantize(target * decimal(valuation.data["fx_rate"]) / conversion, currency)
        adjustment = target - account_balance(account_id, valuation.data["date"])
        key = "valuation/" + valuation.id
        rid = system_id("journal/" + key)
        active_ids.add(rid)
        _system_journal(key, "valuation", account_id, adjustment, valuation.data["date"], conversion, actor,
                        {"valuation_id": valuation.id, "currency": currency}, force_postings=True)
    for journal in journals:
        if journal.id not in active_ids and not journal.deleted:
            before = journal.to_dict()
            journal.deleted = True
            journal.revision += 1
            journal.last_modified = utcnow()
            add_audit("transaction", journal.id, "void", actor, "derived", before, journal.to_dict())
    db.session.flush()


def save_record(kind, data, actor, record_id=None, expected_revision=None, origin="manual", apply_rules=True, preserve_modified=False, replace=False):
    if kind not in KINDS or not isinstance(data, dict):
        raise ApiError("Unsupported record")
    incoming = deepcopy(data)
    rid = record_id or incoming.get("id")
    if rid:
        rid = identifier(rid)
    existing = db.session.get(Record, rid) if rid else None
    if existing and existing.kind != kind:
        raise ApiError("UUID belongs to a different record type", 409, "uuid_conflict")
    if existing and existing.data.get("system") and origin != "derived":
        raise ApiError("System records cannot be changed directly", 409)
    revision = expected_revision if expected_revision is not None else incoming.get("revision")
    if existing and origin == "manual" and revision is None:
        raise ApiError("revision is required when editing", 428, "revision_required")
    if existing and revision is not None and int(revision) != existing.revision:
        raise ApiError("This record changed since it was loaded. Reload and try again.", 409, "revision_conflict", {"current_revision": existing.revision})
    before = existing.to_dict() if existing else None
    previous = {k: deepcopy(v) for k, v in existing.data.items() if k not in META_FIELDS} if existing and not (replace or origin == "import") else {}
    merged = {**previous, **{k: v for k, v in incoming.items() if k not in META_FIELDS}}
    if origin == "manual":
        for field in ("posting_snapshots", "opening_posting_snapshots"):
            merged.pop(field, None)
            if existing and existing.data.get(field):
                merged[field] = deepcopy(existing.data[field])
    currency_change = merged.pop("currency_change", None) if kind == "account" else None
    changing_currency = kind == "account" and existing and merged.get("currency") != existing.data.get("currency")
    if changing_currency:
        if origin == "manual" and currency_change not in ("keep", "relabel"):
            raise ApiError("Choose whether to keep or relabel existing transaction currencies", 409, "currency_choice_required")
        if currency_change == "relabel":
            merged["opening_currency"] = merged["currency"]
    if merged.get("system"):
        raise ApiError("The system field is reserved")
    if kind == "transaction" and existing and "amount" in incoming and incoming["amount"] != existing.data.get("amount") and "destination_amount" not in incoming:
        merged.pop("destination_amount", None)
    explicit_unlink = kind == "transaction" and existing and "subscription_id" in incoming and not incoming["subscription_id"]
    if explicit_unlink:
        merged.pop("subscription_date", None)
        merged.pop("subscription_candidates", None)
    preserve_snapshots = preserve_modified or origin == "import"
    historical = bool(preserve_modified and incoming.get("deleted"))
    _validate(kind, merged, existing, preserve_snapshots, historical)
    if kind == "transaction":
        if not existing and apply_rules:
            merged = apply_transaction_rules(merged)
            _validate_transaction(merged, existing, preserve_snapshots, historical)
        if not explicit_unlink and not incoming.get("deleted") and (apply_rules or merged.get("subscription_id")) and (not existing or any(merged.get(k) != existing.data.get(k) for k in ("subscription_id", "subscription_date", "amount", "date", "source_account_id", "destination_account_id"))):
            _link_subscription(merged, rid)
    record = existing or Record(id=rid or new_id(), kind=kind)
    if not existing:
        db.session.add(record)
    if kind == "account" and existing and existing.data.get("type") in INVESTMENT_TYPES and merged["type"] not in INVESTMENT_TYPES:
        for linked in account_history(record.id) + list_records("subscription", True):
            if linked.data.get("type") != "Return" or linked.data.get("source_account_id") != record.id or linked.data.get("return_account_type"):
                continue
            linked_before = linked.to_dict()
            linked.data = {**linked.data, "return_account_type": existing.data["type"]}
            linked.revision += 1
            linked.last_modified = utcnow()
            add_audit(linked.kind, linked.id, "update", actor, "account-type", linked_before, linked.to_dict())
    record.data = merged
    record.deleted = bool(incoming.get("deleted", False)) if preserve_modified else False
    record.revision = existing.revision + 1 if existing else 1
    record.last_modified = timestamp(incoming["last_modified"]) if preserve_modified and incoming.get("last_modified") else utcnow()
    db.session.flush()
    affected = set()
    if kind == "transaction":
        if record.deleted:
            db.session.query(Posting).filter_by(transaction_id=record.id).delete(synchronize_session=False)
        else:
            _transaction_postings(record)
        affected.update([merged["source_account_id"], merged["destination_account_id"]])
        if before:
            affected.update([before["source_account_id"], before["destination_account_id"]])
    elif kind == "account" and not record.deleted:
        native = decimal(merged["opening_balance"])
        rate = decimal(merged["opening_fx_rate"])
        extra = {"currency": merged["opening_currency"]}
        if merged.get("opening_posting_snapshots"):
            extra["posting_snapshots"] = merged["opening_posting_snapshots"]
        opening = _system_journal("opening/" + record.id, "opening", record.id, native, merged["opening_date"], rate, actor,
                                  extra, force_postings=bool(changing_currency))
        if opening.data.get("posting_snapshots"):
            record.data = {**record.data, "opening_posting_snapshots": opening.data["posting_snapshots"]}
        if changing_currency:
            _change_account_currency(record, before["currency"], currency_change == "relabel", actor)
        affected.add(record.id)
    elif kind == "valuation":
        affected.add(merged["account_id"])
        if before:
            affected.add(before["account_id"])
    for account_id in affected:
        rebuild_account_valuations(account_id, actor)
    add_audit(kind, record.id, "create" if existing is None else "update", actor, origin, before, record.to_dict())
    db.session.flush()
    return record


def _change_account_currency(account, old_currency, relabel, actor):
    """Rebuild native postings; historical transaction amounts never change."""
    currency = account.data["currency"]
    for record in account_history(account.id):
        if record.deleted or record.data.get("system") in ("opening", "valuation"):
            continue
        before = record.to_dict()
        data = deepcopy(record.data)
        if not data.get("system"):
            if relabel:
                if data.get("source_account_id") == account.id:
                    data["currency"] = currency
                    data["fx_rate"] = money(rate_for(currency, data["date"]))
                if data.get("destination_account_id") == account.id:
                    data["destination_currency"] = currency
                    data["destination_fx_rate"] = money(rate_for(currency, data["date"]))
                try:
                    _validate_transaction(data, record)
                except ApiError as error:
                    raise ApiError(f"Cannot relabel transaction '{data.get('description', record.id)}': {error.message}. Keep existing currencies or edit this transaction first.", error.status, error.code)
            record.data = data
            _transaction_postings(record)
        else:
            data["currency"] = currency if relabel else data.get("currency", old_currency)
            if relabel:
                data["amount"] = amount(data["amount"], currency)
                data["fx_rate"] = money(rate_for(currency, data["date"]))
                data["usd_amount"] = money(decimal(data["amount"]) * decimal(data["fx_rate"]))
            record.data = data
            native, usd = decimal(data["amount"]), decimal(data["usd_amount"])
            _replace_postings(record, [(account.id, native, usd), (system_id("reconciliation"), -usd, -usd)])
        if record.data != {k: v for k, v in before.items() if k not in META_FIELDS}:
            record.revision += 1
            record.last_modified = utcnow()
            add_audit("transaction", record.id, "update", actor, "account-currency", before, record.to_dict())
    if relabel:
        for valuation in list_records("valuation"):
            if valuation.data.get("account_id") != account.id:
                continue
            before = valuation.to_dict()
            data = {**valuation.data, "currency": currency,
                    "fx_rate": money(rate_for(currency, valuation.data["date"]))}
            data["amount"] = amount(data["amount"], currency)
            data["usd_amount"] = money(decimal(data["amount"]) * decimal(data["fx_rate"]))
            valuation.data = data
            valuation.revision += 1
            valuation.last_modified = utcnow()
            add_audit("valuation", valuation.id, "update", actor, "account-currency", before, valuation.to_dict())


def void_record(kind, record_id, actor, expected_revision=None, origin="manual", last_modified=None):
    record = get_record(kind, record_id, True)
    if record.data.get("system"):
        raise ApiError("System records cannot be deleted directly", 409)
    if expected_revision is None and origin == "manual":
        raise ApiError("revision is required when deleting", 428, "revision_required")
    if expected_revision is not None and int(expected_revision) != record.revision:
        raise ApiError("This record has changed", 409, "revision_conflict")
    if kind == "account":
        history = account_history(record.id)
        if has_ledger_history(history):
            raise ApiError("Accounts with ledger history must be retained", 409)
    if kind == "account" and any(record.id in (r.data.get("account_id"), r.data.get("source_account_id"), r.data.get("destination_account_id")) for r in list_records("valuation") + list_records("subscription")):
        raise ApiError("This account is referenced by a valuation or subscription", 409)
    if kind == "account" and any(item.get("account_id") == record.id for r in list_records("scenario") for field in ("assumptions", "flows") for item in r.data.get(field, [])):
        raise ApiError("This account is referenced by a scenario", 409)
    if kind == "account" and any(c.get("field") in ("source_account_id", "destination_account_id") and c.get("value") == record.id for r in list_records("rule") for c in r.data.get("conditions", [])):
        raise ApiError("This account is referenced by a rule", 409)
    if kind == "currency" and _currency_referenced(record.data["code"]):
        raise ApiError("This currency is still in use", 409)
    if kind in ("category", "tag", "subscription"):
        field = {"category": "category_id", "tag": "tag_ids", "subscription": "subscription_id"}[kind]
        if any(record.id == r.data.get(field) or (isinstance(r.data.get(field), list) and record.id in r.data[field]) for r in list_records("transaction") + list_records("budget") + list_records("subscription")):
            raise ApiError("This record is still referenced; remove those links first", 409)
    before = record.to_dict()
    record.deleted = True
    record.revision += 1
    record.last_modified = timestamp(last_modified) if last_modified else utcnow()
    affected = set()
    if kind == "account":
        for journal in history:
            if not journal.deleted:
                journal_before = journal.to_dict()
                journal.deleted = True
                journal.revision += 1
                journal.last_modified = record.last_modified
                db.session.query(Posting).filter_by(transaction_id=journal.id).delete(synchronize_session=False)
                add_audit("transaction", journal.id, "void", actor, "derived", journal_before, journal.to_dict())
    if kind == "transaction":
        db.session.query(Posting).filter_by(transaction_id=record.id).delete(synchronize_session=False)
        affected.update([record.data["source_account_id"], record.data["destination_account_id"]])
    elif kind == "valuation":
        affected.add(record.data["account_id"])
    db.session.flush()
    for account_id in affected:
        rebuild_account_valuations(account_id, actor)
    add_audit(kind, record.id, "void", actor, origin, before, record.to_dict())
    return record


delete_record = void_record


def reconcile_account(account_id, balance, when, actor, expected_revision):
    account = get_record("account", account_id)
    if int(expected_revision or 0) != account.revision:
        raise ApiError("This account has changed", 409, "revision_conflict")
    when = date_string(when)
    target = decimal(amount(balance, account.data["currency"], "balance"))
    if account.data["account_class"] == "liability":
        target = -target
    difference = target - account_balance(account.id, when)
    journal = _system_journal("reconciliation/" + new_id(), "reconciliation", account.id, difference, when,
                              rate_for(account.data["currency"], when), actor)
    before = account.to_dict()
    account.revision += 1
    account.last_modified = utcnow()
    rebuild_account_valuations(account.id, actor)
    add_audit("account", account.id, "reconcile", actor, "manual", before, account.to_dict())
    return journal
