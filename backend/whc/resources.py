from datetime import date
from flask import Blueprint, current_app, g, jsonify, request

from .auth import login_required, admin_required
from .models import db, Posting
from .ledger import (get_record, list_records, public_record, save_record, void_record,
                     apply_transaction_rules, reconcile_account)
from .recurrence import occurrences
from .validation import ApiError, ACCOUNT_TYPES, INVESTMENT_TYPES, TRANSACTION_TYPES, currency_definitions, date_string

bp = Blueprint("resources", __name__)
RESOURCE_KINDS = {"accounts": "account", "transactions": "transaction", "categories": "category", "tags": "tag",
                  "subscriptions": "subscription", "rules": "rule", "budgets": "budget", "rates": "rate",
                  "valuations": "valuation", "scenarios": "scenario", "currencies": "currency"}


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object")
    return data


def kind_for(resource):
    if resource not in RESOURCE_KINDS:
        raise ApiError("Resource not found", 404, "not_found")
    return RESOURCE_KINDS[resource]


@bp.get("/meta")
@login_required
def metadata():
    return jsonify(account_types=ACCOUNT_TYPES, investment_account_types=INVESTMENT_TYPES, transaction_types=TRANSACTION_TYPES,
                   account_classes=["asset", "liability", "income", "expense", "equity"],
                   currencies=currency_definitions(),
                   timezone=current_app.config.get("HOUSEHOLD_TIMEZONE", "America/New_York"), default_currency="USD")


@bp.get("/<resource>")
@login_required
def collection(resource):
    kind = kind_for(resource)
    if kind == "currency":
        items = currency_definitions()
        return jsonify(items=items, total=len(items), page=1, page_size=len(items))
    records = list_records(kind)
    if kind == "transaction" and request.args.get("include_system") != "true":
        records = [record for record in records if not record.data.get("system")]
    query = request.args.get("q", "").casefold()
    if query:
        records = [record for record in records if query in " ".join(str(record.data.get(k, "")) for k in ("name", "description", "notes")).casefold()]
    for field in ("category_id", "subscription_id", "type", "account_class", "currency", "month"):
        if request.args.get(field):
            records = [r for r in records if r.data.get(field) == request.args[field]]
    if request.args.get("tag_id"):
        records = [r for r in records if request.args["tag_id"] in r.data.get("tag_ids", [])]
    if request.args.get("account_id"):
        account_id = request.args["account_id"]
        records = [r for r in records if account_id in (r.data.get("source_account_id"), r.data.get("destination_account_id"), r.data.get("account_id"))]
    for query_field, comparison in (("start", lambda a, b: a >= b), ("end", lambda a, b: a <= b)):
        when = request.args.get(query_field) or request.args.get(query_field + "_date")
        if when:
            date_string(when)
            records = [r for r in records if comparison(r.data.get("date", ""), when)]
    if kind == "transaction":
        records.sort(key=lambda r: (r.data.get("date", ""), r.last_modified), reverse=True)
    elif kind in ("account", "category", "tag", "subscription", "rule", "scenario"):
        records.sort(key=lambda r: r.data.get("name", "").casefold())
    try:
        page, size = int(request.args.get("page", 1)), int(request.args.get("page_size", 50))
        if page < 1 or not 1 <= size <= 500:
            raise ValueError()
    except ValueError:
        raise ApiError("page must be positive and page_size between 1 and 500")
    return jsonify(items=[public_record(r) for r in records[(page - 1) * size:page * size]], total=len(records), page=page, page_size=size)


@bp.get("/<resource>/<record_id>")
@login_required
def detail(resource, record_id):
    return jsonify(public_record(get_record(kind_for(resource), record_id)))


@bp.post("/<resource>")
@login_required
def create(resource):
    if resource == "currencies" and g.user.role != "admin":
        raise ApiError("Administrator access is required", 403, "forbidden")
    record = save_record(kind_for(resource), body(), g.user)
    db.session.commit()
    return jsonify(public_record(record)), 201


@bp.patch("/<resource>/<record_id>")
@login_required
def update(resource, record_id):
    if resource == "currencies" and g.user.role != "admin":
        raise ApiError("Administrator access is required", 403, "forbidden")
    data = body()
    get_record(kind_for(resource), record_id)
    record = save_record(kind_for(resource), data, g.user, record_id=record_id, expected_revision=data.get("revision"))
    db.session.commit()
    return jsonify(public_record(record))


@bp.delete("/<resource>/<record_id>")
@login_required
def remove(resource, record_id):
    if resource == "currencies" and g.user.role != "admin":
        raise ApiError("Administrator access is required", 403, "forbidden")
    data = request.get_json(silent=True) or {}
    record = void_record(kind_for(resource), record_id, g.user, data.get("revision"))
    db.session.commit()
    return jsonify(record.to_dict())


@bp.post("/accounts/<account_id>/reconcile")
@login_required
def reconcile(account_id):
    data = body()
    journal = reconcile_account(account_id, data.get("balance"), data.get("date"), g.user, data.get("revision"))
    db.session.commit()
    return jsonify(account=public_record(get_record("account", account_id)), adjustment=journal.to_dict())


@bp.get("/subscriptions/<subscription_id>/occurrences")
@login_required
def subscription_occurrences(subscription_id):
    subscription = get_record("subscription", subscription_id)
    start = date.fromisoformat(date_string(request.args.get("start", date.today().isoformat())))
    end = date.fromisoformat(date_string(request.args.get("end", f"{start.year}-12-31")))
    used = {r.data.get("subscription_date"): r.id for r in list_records("transaction") if r.data.get("subscription_id") == subscription_id}
    return jsonify(items=[{"date": when.isoformat(), "transaction_id": used.get(when.isoformat()), "fulfilled": when.isoformat() in used} for when in occurrences(subscription.data, start, end)])


@bp.post("/rules/<rule_id>/preview")
@login_required
def preview_rule(rule_id):
    rule = get_record("rule", rule_id)
    data = body()
    transaction = data.get("transaction", {})
    if not isinstance(transaction, dict):
        raise ApiError("transaction must be an object")
    return jsonify(before=transaction, after=apply_transaction_rules(transaction, [rule]))


@bp.post("/rules/<rule_id>/reapply")
@login_required
def reapply_rule(rule_id):
    rule = get_record("rule", rule_id)
    ids = body().get("transaction_ids", [])
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500:
        raise ApiError("Choose between 1 and 500 transaction_ids")
    changed = []
    for record_id in dict.fromkeys(ids):
        record = get_record("transaction", record_id)
        if record.data.get("system"):
            continue
        data = apply_transaction_rules(record.data, [rule])
        if data.get("applied_rule_ids"):
            changed.append(save_record("transaction", data, g.user, record.id, record.revision, "rule", False).to_dict())
    db.session.commit()
    return jsonify(items=changed, total=len(changed))
