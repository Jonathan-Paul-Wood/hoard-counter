from datetime import date
from decimal import Decimal
import pytest

from whc import create_app
from whc.models import db, Posting, AuditEvent
from whc.ledger import (save_record, get_record, list_records, account_balance,
                        system_id, public_record, void_record)
from whc.recurrence import occurrences
from whc.validation import ApiError


@pytest.fixture
def app(tmp_path):
    application = create_app({"TESTING": True, "DATABASE_URL": "sqlite:///:memory:",
                              "DATA_DIR": str(tmp_path), "UPLOAD_DIR": str(tmp_path / "uploads"),
                              "BACKUP_DIR": str(tmp_path / "backups"), "COOKIE_SECURE": False})
    with application.app_context():
        yield application
        db.session.remove()
        db.drop_all()


def account(name="Checking", opening="100", currency="USD", typ="Checking"):
    return save_record("account", {"name": name, "type": typ, "currency": currency,
                                  "opening_balance": opening, "opening_date": "2025-01-01"}, None)


def transaction(source, destination, amount="10", when="2025-01-05", typ="Transfer", **extra):
    return save_record("transaction", {"source_account_id": source, "destination_account_id": destination,
                                       "amount": amount, "date": when, "type": typ,
                                       "description": "Example transaction", **extra}, None)


def assert_balanced():
    for record in list_records("transaction"):
        postings = db.session.scalars(db.select(Posting).where(Posting.transaction_id == record.id)).all()
        assert len(postings) >= 2
        assert sum((Decimal(p.usd_amount) for p in postings), Decimal(0)) == 0


def test_double_entry_income_expense_transfer_liability(app):
    checking, savings = account(), account("Savings", "0", typ="Savings")
    debt = account("Card", "50", typ="Credit Card")
    transaction(system_id("income"), checking.id, "100", typ="Income")
    transaction(checking.id, system_id("expense"), "20", typ="Expense")
    transaction(checking.id, savings.id, "30")
    transaction(checking.id, debt.id, "10")
    assert account_balance(checking.id) == Decimal("140")
    assert account_balance(savings.id) == Decimal("30")
    assert account_balance(debt.id) == Decimal("-40")
    assert Decimal(public_record(debt)["balance"]) == Decimal("40")
    assert_balanced()


def test_fx_exact_precision_and_snapshots(app):
    rate = save_record("rate", {"currency": "ETH", "date": "2025-01-01", "rate": "3000"}, None)
    checking, wallet = account(), account("ETH", "0", "ETH", "Crypto Cold Wallet")
    tx = transaction(checking.id, wallet.id, "0.01")
    assert tx.data["destination_amount"] == "0.000003333333333333"
    assert account_balance(wallet.id) == Decimal("0.000003333333333333")
    postings = db.session.scalars(db.select(Posting).where(Posting.transaction_id == tx.id)).all()
    assert len(postings) == 3
    save_record("rate", {"rate": "4000"}, None, rate.id, rate.revision)
    save_record("transaction", {"notes": "Updated note"}, None, tx.id, tx.revision)
    assert tx.data["destination_fx_rate"] == "3000"
    assert_balanced()


def test_absolute_valuations_replay_after_retroactive_contribution(app):
    investment = account("Brokerage", "100", typ="Brokerage")
    checking = account()
    transaction(system_id("income"), investment.id, "5", "2025-01-10", "Return")
    valuation = save_record("valuation", {"account_id": investment.id, "date": "2025-01-31", "amount": "110"}, None)
    adjustment = get_record("transaction", system_id("journal/valuation/" + valuation.id))
    assert Decimal(adjustment.data["amount"]) == Decimal("5")
    transaction(checking.id, investment.id, "20", "2025-01-15")
    assert Decimal(adjustment.data["amount"]) == Decimal("-15")
    assert account_balance(investment.id) == Decimal("110")
    assert_balanced()


def test_revision_conflict_and_edit_changes_both_postings(app):
    source, dest = account(), account("Savings", "0", typ="Savings")
    tx = transaction(source.id, dest.id)
    old_revision = tx.revision
    save_record("transaction", {"amount": "12"}, None, tx.id, old_revision)
    assert account_balance(source.id) == Decimal("88")
    assert account_balance(dest.id) == Decimal("12")
    with pytest.raises(ApiError) as error:
        save_record("transaction", {"amount": "14"}, None, tx.id, old_revision)
    assert error.value.code == "revision_conflict"
    assert db.session.scalar(db.select(db.func.count()).select_from(AuditEvent)) > 0
    assert_balanced()


def test_rules_subscription_unique_claim_and_month_end(app):
    checking = account()
    category = save_record("category", {"name": "Internet"}, None)
    sub = save_record("subscription", {"name": "Internet", "type": "Expense", "source_account_id": checking.id,
                        "destination_account_id": system_id("expense"), "amount": "10", "start_date": "2025-01-31",
                        "frequency": "monthly", "interval": 1, "day_tolerance": 3}, None)
    save_record("rule", {"name": "Classify Internet", "conditions": [{"field": "description", "operator": "contains", "value": "Example"}],
                         "actions": {"category_id": category.id}}, None)
    tx = transaction(checking.id, system_id("expense"), "10", "2025-02-28", "Expense")
    assert tx.data["category_id"] == category.id
    assert tx.data["subscription_id"] == sub.id
    assert tx.data["subscription_date"] == "2025-02-28"
    second = transaction(checking.id, system_id("expense"), "10", "2025-02-28", "Expense")
    assert not second.data.get("subscription_id")
    save_record("transaction", {"subscription_id": None}, None, tx.id, tx.revision)
    assert not tx.data.get("subscription_id")
    assert not tx.data.get("subscription_date")
    assert occurrences(sub.data, date(2025, 1, 1), date(2025, 3, 31)) == [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)]


def test_import_snapshot_preserved_and_float_rejected(app):
    save_record("rate", {"currency": "EUR", "date": "2025-01-01", "rate": "2"}, None)
    euros = save_record("account", {"name": "Euros", "type": "Savings", "currency": "EUR", "opening_balance": "10", "opening_date": "2025-01-01", "opening_fx_rate": "1.5"}, None, origin="import", preserve_modified=True)
    assert euros.data["opening_fx_rate"] == "1.5"
    dollars = account()
    tx = save_record("transaction", {"source_account_id": euros.id, "destination_account_id": dollars.id,
                  "amount": "1", "destination_amount": "1.5", "date": "2025-01-05", "type": "Transfer", "description": "Imported",
                  "fx_rate": "1.5", "destination_fx_rate": "1"}, None, origin="import", apply_rules=False, preserve_modified=True)
    assert tx.data["usd_amount"] == "1.5"
    with pytest.raises(ApiError):
        transaction(euros.id, dollars.id, 0.1)


def test_auth_csrf_and_maintenance(app, tmp_path):
    client = app.test_client()
    assert client.get("/api/v1/accounts").status_code == 401
    response = client.post("/api/v1/auth/setup", json={"username": "owner", "password": "correct-horse-battery"})
    assert response.status_code == 201
    token = response.json["csrf_token"]
    assert client.post("/api/v1/categories", json={"name": "Food"}).status_code == 403
    response = client.post("/api/v1/categories", json={"name": "Food"}, headers={"X-CSRF-Token": token})
    assert response.status_code == 201
    assert client.post("/api/v1/categories", json={"name": "Food"}, headers={"X-CSRF-Token": token, "Origin": "https://evil.example"}).status_code == 403
    forwarded = {"X-CSRF-Token": token, "X-Forwarded-Host": "budget.example:3000", "X-Forwarded-Proto": "https", "Origin": "https://budget.example:3000"}
    assert client.post("/api/v1/categories", json={"name": "Through proxy"}, headers=forwarded).status_code == 201
    assert client.post("/api/v1/categories", json={"name": "Bad origin"}, headers={**forwarded, "Origin": "https://evil.example"}).status_code == 403
    (tmp_path / ".maintenance").write_text("maintenance")
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/v1/categories", json={"name": "Blocked"}, headers={"X-CSRF-Token": token}).status_code == 503


def test_account_combinations_refund_and_fee_validation(app):
    checking = account()
    with pytest.raises(ApiError):
        transaction(checking.id, system_id("expense"), typ="Transfer")
    with pytest.raises(ApiError):
        transaction(system_id("expense"), checking.id, typ="Expense")
    refund = transaction(system_id("expense"), checking.id, typ="Expense", is_refund=True)
    assert refund.data["is_refund"] is True
    fee = transaction(checking.id, system_id("expense"), typ="Expense", investment_fee=True)
    assert fee.data["investment_fee"] is True
    with pytest.raises(ApiError):
        transaction(system_id("income"), checking.id, typ="Return", investment_fee=True)
    assert_balanced()


def test_missing_rates_and_same_currency_edit_retains_snapshot(app):
    euros = account("Euro checking", "0", "EUR")
    euro_savings = account("Euro savings", "0", "EUR", "Savings")
    with pytest.raises(ApiError) as error:
        transaction(euros.id, euro_savings.id)
    assert error.value.code == "missing_rate"
    rate = save_record("rate", {"currency": "EUR", "date": "2025-01-01", "rate": "1.1"}, None)
    tx = transaction(euros.id, euro_savings.id)
    save_record("rate", {"rate": "1.2"}, None, rate.id, rate.revision)
    save_record("transaction", {"amount": "20"}, None, tx.id, tx.revision)
    assert Decimal(tx.data["usd_amount"]) == Decimal("22")
    assert Decimal(tx.data["destination_usd_amount"]) == Decimal("22")
    assert Decimal(tx.data["destination_amount"]) == Decimal("20")
    assert_balanced()


def test_changing_destination_rebuilds_both_valuation_series(app):
    checking, first, second = account(), account("First", typ="Brokerage"), account("Second", typ="Brokerage")
    tx = transaction(checking.id, first.id, "20", "2025-01-15")
    v1 = save_record("valuation", {"account_id": first.id, "date": "2025-01-31", "amount": "130"}, None)
    v2 = save_record("valuation", {"account_id": second.id, "date": "2025-01-31", "amount": "105"}, None)
    save_record("transaction", {"destination_account_id": second.id}, None, tx.id, tx.revision)
    assert account_balance(first.id) == Decimal("130")
    assert account_balance(second.id) == Decimal("105")
    assert Decimal(get_record("transaction", system_id("journal/valuation/" + v1.id)).data["amount"]) == Decimal("30")
    assert Decimal(get_record("transaction", system_id("journal/valuation/" + v2.id)).data["amount"]) == Decimal("-15")
    assert_balanced()


def test_custom_currency_and_in_use_precision_protection(app):
    custom = save_record("currency", {"code": "PLN", "name": "Polish Zloty", "precision": 2}, None)
    save_record("rate", {"currency": "PLN", "date": "2025-01-01", "rate": "0.25"}, None)
    zloty = account("Poland", "100", "PLN", "Wallet")
    assert Decimal(public_record(zloty)["balance_usd"]) == Decimal("25")
    with pytest.raises(ApiError):
        save_record("currency", {"precision": 4}, None, custom.id, custom.revision)
    with pytest.raises(ApiError):
        void_record("currency", custom.id, None, custom.revision)


def test_rule_tag_condition_uses_uuid_membership(app):
    checking = account()
    tag = save_record("tag", {"name": "Household"}, None)
    category = save_record("category", {"name": "Shared"}, None)
    save_record("rule", {"name": "Tagged household expense", "conditions": [{"field": "tag_ids", "operator": "contains", "value": tag.id}],
                         "actions": {"category_id": category.id}}, None)
    matched = transaction(checking.id, system_id("expense"), typ="Expense", tag_ids=[tag.id])
    assert matched.data["category_id"] == category.id
    unmatched = transaction(checking.id, system_id("expense"), typ="Expense")
    assert unmatched.data.get("category_id") is None


def test_tombstone_transaction_can_refer_to_deleted_category(app):
    checking = account()
    category = save_record("category", {"name": "Old category"}, None)
    tx = transaction(checking.id, system_id("expense"), typ="Expense", category_id=category.id)
    void_record("transaction", tx.id, None, tx.revision)
    void_record("category", category.id, None, category.revision)
    exported = tx.to_dict()
    restored = save_record("transaction", exported, None, tx.id, tx.revision, origin="import", apply_rules=False, preserve_modified=True)
    assert restored.deleted
    assert db.session.scalar(db.select(db.func.count()).select_from(Posting).where(Posting.transaction_id == tx.id)) == 0


def test_concurrent_explicit_subscription_occurrence_claims(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    application = create_app({"TESTING": True, "DATABASE_URL": "sqlite:///" + str(tmp_path / "concurrent.db").replace("\\", "/"),
                              "DATA_DIR": str(tmp_path), "UPLOAD_DIR": str(tmp_path / "uploads"),
                              "BACKUP_DIR": str(tmp_path / "backups"), "COOKIE_SECURE": False})
    setup_client = application.test_client()
    assert setup_client.post("/api/v1/auth/setup", json={"username": "owner", "password": "correct-horse-battery"}).status_code == 201
    with application.app_context():
        checking = account()
        sub = save_record("subscription", {"name": "Only once", "source_account_id": checking.id,
                          "destination_account_id": system_id("expense"), "amount": "10", "start_date": "2025-01-31"}, None)
        payload = {"source_account_id": checking.id, "destination_account_id": system_id("expense"), "amount": "10",
                   "description": "Concurrent", "date": "2025-01-31", "type": "Expense", "subscription_id": sub.id,
                   "subscription_date": "2025-01-31"}
        db.session.commit()
    clients = [application.test_client(), application.test_client()]
    tokens = [client.post("/api/v1/auth/login", json={"username": "owner", "password": "correct-horse-battery"}).json["csrf_token"] for client in clients]
    def create(index):
        return clients[index].post("/api/v1/transactions", json=payload, headers={"X-CSRF-Token": tokens[index]}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(create, range(2)))
    assert sorted(statuses) == [201, 409]
    with application.app_context():
        assert len([t for t in list_records("transaction") if not t.data.get("system")]) == 1
        assert_balanced()
        db.session.remove()
        db.engine.dispose()


def test_migration_is_repeatable_and_preserves_records(tmp_path, monkeypatch):
    from pathlib import Path
    from alembic.config import Config
    from alembic import command
    database_url = "sqlite:///" + str(tmp_path / "migrated.db").replace("\\", "/")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    command.upgrade(config, "head")
    app_config = {"DATABASE_URL": database_url, "DATA_DIR": str(tmp_path), "UPLOAD_DIR": str(tmp_path / "uploads"),
                  "BACKUP_DIR": str(tmp_path / "backups"), "COOKIE_SECURE": False, "SECRET_KEY": "migration-test-secret"}
    application = create_app(app_config)
    with application.app_context():
        created = account("Survives rebuild")
        identity = created.id
        db.session.commit()
        db.session.remove()
        db.engine.dispose()
    command.upgrade(config, "head")
    application2 = create_app(app_config)
    assert application2.test_client().get("/api/health").status_code == 200
    with application2.app_context():
        assert get_record("account", identity).data["name"] == "Survives rebuild"
        assert account_balance(identity) == Decimal("100")
        db.session.remove()
        db.engine.dispose()


def test_public_account_excludes_future_postings_and_valuations(app, monkeypatch):
    from datetime import datetime as RealDatetime, timezone
    from whc.analytics import balance_summary
    class FrozenDatetime(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            instant = RealDatetime(2026, 9, 16, 16, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr("whc.ledger.datetime", FrozenDatetime)
    checking = account()
    save_record("valuation", {"account_id": checking.id, "date": "2026-09-15", "amount": "95"}, None)
    transaction(checking.id, system_id("expense"), "10", "2026-09-16", "Expense")
    transaction(checking.id, system_id("expense"), "30", "2026-09-17", "Expense")
    save_record("valuation", {"account_id": checking.id, "date": "2026-09-17", "amount": "999"}, None)
    row = public_record(checking)
    dashboard_row = next(r for r in balance_summary(date(2026, 9, 16))["accounts"] if r["id"] == checking.id)
    assert Decimal(row["balance"]) == Decimal("85")
    assert row["balance"] == dashboard_row["balance"]
    assert row["balance_usd"] == dashboard_row["balance_usd"]
    assert row["as_of"] == "2026-09-16"
    assert row["valuation_date"] == "2026-09-15"


def test_public_account_fx_uses_household_calendar_date(app, monkeypatch):
    from datetime import datetime as RealDatetime, timezone
    class FrozenDatetime(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            instant = RealDatetime(2026, 9, 17, 1, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    class HostDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 17)
    monkeypatch.setattr("whc.ledger.datetime", FrozenDatetime)
    monkeypatch.setattr("whc.ledger.date", HostDate)
    save_record("rate", {"currency": "EUR", "date": "2025-01-01", "rate": "1"}, None)
    save_record("rate", {"currency": "EUR", "date": "2026-09-16", "rate": "2"}, None)
    save_record("rate", {"currency": "EUR", "date": "2026-09-17", "rate": "3"}, None)
    euros = account("Euros", "10", "EUR")
    app.config["HOUSEHOLD_TIMEZONE"] = "America/New_York"
    row = public_record(euros)
    assert row["as_of"] == "2026-09-16"
    assert Decimal(row["balance_usd"]) == Decimal("20")
    app.config["HOUSEHOLD_TIMEZONE"] = "Asia/Tokyo"
    row = public_record(euros)
    assert row["as_of"] == "2026-09-17"
    assert Decimal(row["balance_usd"]) == Decimal("30")


def test_zero_foreign_balance_known_without_fx_and_derived_fields_not_saved(app):
    euros = account("Empty Euros", "0", "EUR")
    row = public_record(euros)
    assert row["balance_usd"] == "0"
    assert row["signed_balance_usd"] == "0"
    assert row["missing_rate"] is False
    assert row["valuation_date"] is None
    updated = save_record("account", {**row, "notes": "Keep native account information"}, None, euros.id, euros.revision)
    assert not {"as_of", "valuation_date", "missing_rate", "balance", "balance_usd"} & set(updated.data)
