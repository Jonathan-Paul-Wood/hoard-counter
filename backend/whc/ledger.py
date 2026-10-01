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
from .validation import (ApiError, ACCOUNT_TYPES, ASSET_TYPES, LIABILITY_TYPES,
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


def _validate_account(data, existing, preserve_snapshots=False):
    required_text(data, "name", 160)
    if data.get("type") not in ACCOUNT_TYPES:
        raise ApiError("Choose a supported account type")
    inferred = "asset" if data["type"] in ASSET_TYPES else "liability" if data["type"] in LIABILITY_TYPES else {"Income Source": "income", "Expense Payee": "expense", "Equity": "equity"}[data["type"]]
    data["account_class"] = data.get("account_class", inferred)
    if data["account_class"] != inferred:
        raise ApiError("Account type and accounting class do not agree")
    data["currency"] = currency_code(data.get("currency", "USD"))
    data["opening_balance"] = amount(data.get("opening_balance", "0"), data["currency"], "opening_balance")
    if inferred == "liability" and decimal(data["opening_balance"]) > 0:
        data["opening_balance"] = money(-decimal(data["opening_balance"]))
    data["opening_date"] = date_string(data.get("opening_date", date.today().isoformat()), "opening_date")
    changed = not existing or any(data.get(k) != existing.data.get(k) for k in ("opening_date", "currency")) or (existing and not decimal(existing.data.get("opening_balance", "0")) and decimal(data["opening_balance"]))
    if (changed and not (preserve_snapshots and data.get("opening_fx_rate"))) or not data.get("opening_fx_rate"):
        data["opening_fx_rate"] = money(rate_for(data["currency"], data["opening_date"])) if decimal(data["opening_balance"]) else "1"
    if decimal(data["opening_fx_rate"], "opening_fx_rate") <= 0:
        raise ApiError("Opening conversion rate must be positive")
    if existing and existing.data.get("system"):
        raise ApiError("Built-in counterpart accounts cannot be edited", 409)
    if existing and any(data[key] != existing.data.get(key) for key in ("currency", "account_class")):
        if db.session.scalar(select(Posting.id).where(Posting.account_id == existing.id).limit(1)):
            raise ApiError("An account's currency and accounting class cannot change after it has postings", 409)


def _validate_transaction(data, existing=None, preserve_snapshots=False, historical=False):
    source = _reference(data, "source_account_id", "account", True, historical)
    destination = _reference(data, "destination_account_id", "account", True, historical)
    if source.id == destination.id:
        raise ApiError("Source and destination must be different accounts")
    if data.get("type") not in TRANSACTION_TYPES:
        raise ApiError("Choose Income, Expense, Transfer, or Return")
    required_text(data, "description", 1000)
    data["date"] = date_string(data.get("date"))
    sclass, dclass = source.data["account_class"], destination.data["account_class"]
    financial = {"asset", "liability"}
    valid = {"Income": sclass == "income" and dclass in financial,
             "Return": sclass == "income" and dclass == "asset",
             "Transfer": sclass in financial and dclass in financial,
             "Expense": (sclass in financial and dclass == "expense") or (bool(data.get("is_refund")) and sclass == "expense" and dclass in financial)}
    if not valid[data["type"]]:
        raise ApiError("Transaction type does not match its source and destination account classes")
    if data.get("is_refund") and data["type"] != "Expense":
        raise ApiError("Only expenses may be marked as refunds")
    if data.get("investment_fee") and (data["type"] != "Expense" or data.get("is_refund")):
        raise ApiError("Investment fees must be outgoing expenses")
    data["amount"] = amount(data.get("amount"), source.data["currency"], positive=True)
    # Preserve the exact saved conversion when an unrelated field is edited.
    s_changed = not existing or any(data.get(k) != existing.data.get(k) for k in ("date", "source_account_id"))
    d_changed = not existing or any(data.get(k) != existing.data.get(k) for k in ("date", "destination_account_id"))
    if (s_changed and not (preserve_snapshots and data.get("fx_rate"))) or "fx_rate" not in data:
        data["fx_rate"] = money(rate_for(source.data["currency"], data["date"]))
    if (d_changed and not (preserve_snapshots and data.get("destination_fx_rate"))) or "destination_fx_rate" not in data:
        data["destination_fx_rate"] = money(rate_for(destination.data["currency"], data["date"]))
    if decimal(data["fx_rate"], "fx_rate") <= 0 or decimal(data["destination_fx_rate"], "destination_fx_rate") <= 0:
        raise ApiError("Conversion rates must be positive")
    data["usd_amount"] = money(decimal(data["amount"]) * decimal(data["fx_rate"]))
    if not data.get("destination_amount"):
        data["destination_amount"] = money(quantize(decimal(data["usd_amount"]) / decimal(data["destination_fx_rate"]), destination.data["currency"]))
    data["destination_amount"] = amount(data["destination_amount"], destination.data["currency"], "destination_amount", positive=True)
    data["destination_usd_amount"] = money(decimal(data["destination_amount"]) * decimal(data["destination_fx_rate"]))
    data["currency"] = source.data["currency"]
    data["destination_currency"] = destination.data["currency"]
    if source.data["currency"] == destination.data["currency"] and decimal(data["amount"]) != decimal(data["destination_amount"]):
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
        if data["source_account_id"] == data["destination_account_id"]:
            raise ApiError("Subscription accounts must differ")
        data["currency"] = currency_code(data.get("currency", source.data["currency"]))
        if data["currency"] != source.data["currency"]:
            raise ApiError("Subscription amount uses the source account currency")
        data["amount"] = amount(data.get("amount"), data["currency"], positive=True)
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
        data["amount"] = amount(data.get("amount"), account.data["currency"])
        if decimal(data["amount"]) < 0:
            raise ApiError("Market valuation cannot be negative")
        if not historical and any(r.id != (existing.id if existing else None) and r.data["account_id"] == account.id and r.data["date"] == data["date"] for r in list_records("valuation")):
            raise ApiError("An account already has a valuation for this date", 409)
        changed = not existing or any(data.get(k) != existing.data.get(k) for k in ("account_id", "date"))
        if (changed and not (preserve_snapshots and data.get("fx_rate"))) or not data.get("fx_rate"):
            data["fx_rate"] = money(rate_for(account.data["currency"], data["date"]))
        data["usd_amount"] = money(decimal(data["amount"]) * decimal(data["fx_rate"]))
        data["currency"] = account.data["currency"]
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
    return any(r.data.get("currency") == code for kind in ("account", "subscription", "rate") for r in list_records(kind))


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
        if not explicit and (not sub.get("active", True) or any(sub.get(k) != data.get(k) for k in ("source_account_id", "destination_account_id", "type")) or abs(decimal(sub["amount"]) - decimal(data["amount"])) > decimal(sub.get("amount_tolerance", "0"))):
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


def _replace_postings(record, entries):
    if sum((value[2] for value in entries), Decimal(0)) != 0:
        raise ApiError("Journal postings do not balance", 500, "unbalanced_journal")
    db.session.query(Posting).filter_by(transaction_id=record.id).delete(synchronize_session=False)
    for account_id, native, usd in entries:
        db.session.add(Posting(transaction_id=record.id, account_id=account_id,
                               native_amount=money(native), usd_amount=money(usd)))
    db.session.flush()


def _transaction_postings(record):
    data = record.data
    source_usd, destination_usd = decimal(data["usd_amount"]), decimal(data["destination_usd_amount"])
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
            "description": {"opening": "Opening balance", "valuation": "Market valuation adjustment", "reconciliation": "Balance reconciliation"}[system],
            "amount": money(native), "usd_amount": money(native * rate), "fx_rate": money(rate), **(extra or {})}
    if not record:
        record = Record(id=rid, kind="transaction", data=data)
        db.session.add(record)
    elif record.data == data and not record.deleted:
        if force_postings:
            usd = native * rate
            _replace_postings(record, [(account_id, native, usd), (system_id(system), -usd, -usd)])
        return record
    else:
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
        adjustment = target - account_balance(account_id, valuation.data["date"])
        key = "valuation/" + valuation.id
        rid = system_id("journal/" + key)
        active_ids.add(rid)
        _system_journal(key, "valuation", account_id, adjustment, valuation.data["date"], decimal(valuation.data["fx_rate"]), actor,
                        {"valuation_id": valuation.id}, force_postings=True)
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
        _system_journal("opening/" + record.id, "opening", record.id, native, merged["opening_date"], rate, actor)
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


def void_record(kind, record_id, actor, expected_revision=None, origin="manual", last_modified=None):
    record = get_record(kind, record_id, True)
    if record.data.get("system"):
        raise ApiError("System records cannot be deleted directly", 409)
    if expected_revision is None and origin == "manual":
        raise ApiError("revision is required when deleting", 428, "revision_required")
    if expected_revision is not None and int(expected_revision) != record.revision:
        raise ApiError("This record has changed", 409, "revision_conflict")
    if kind == "account" and db.session.scalar(select(Posting.id).where(Posting.account_id == record.id).limit(1)):
        raise ApiError("Accounts with ledger history must be retained", 409)
    if kind == "account" and any(record.id in (r.data.get("account_id"), r.data.get("source_account_id"), r.data.get("destination_account_id")) for r in list_records("valuation") + list_records("subscription")):
        raise ApiError("This account is referenced by a valuation or subscription", 409)
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
