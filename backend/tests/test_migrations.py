from datetime import datetime
from decimal import Decimal
import json
import shutil
import sqlite3
from uuid import UUID

from alembic import command
import pytest
from sqlalchemy.exc import OperationalError

from whc import create_app, migrations


@pytest.fixture
def installation(tmp_path, monkeypatch):
    directory = tmp_path / "migrations"
    shutil.copytree(migrations.BACKEND / "migrations", directory, ignore=shutil.ignore_patterns("__pycache__"))
    base_config = migrations.migration_config

    def migration_config():
        config = base_config()
        config.set_main_option("script_location", directory.as_posix())
        return config

    monkeypatch.setattr(migrations, "migration_config", migration_config)
    database = tmp_path / "household.db"
    url = "sqlite:///" + database.as_posix()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", url)
    app_config = dict(DATABASE_URL=url, DATA_DIR=str(tmp_path), UPLOAD_DIR=str(tmp_path / "uploads"),
                      BACKUP_DIR=str(tmp_path / "backups"), SECRET_KEY="local-test-secret")
    return migration_config(), directory / "versions", database, app_config


def add_revision(directory, name, revision, parent, sql):
    (directory / name).write_text(
        f"from alembic import op\nrevision = {revision!r}\ndown_revision = {parent!r}\n\ndef upgrade():\n"
        + "".join(f"    op.execute({statement!r})\n" for statement in sql),
        encoding="utf-8",
    )


def test_startup_upgrades_legacy_database_and_audits_each_revision_once(installation):
    config, directory, database, app_config = installation
    command.upgrade(config, "0001_initial")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE records SET revision = 7")

    # The file is older than its parent, as can happen when integrating branches.
    name = "2026-10-02-12-34-5678-create-example.py"
    add_revision(directory, name, "test_example", migrations.schema_head(), ["CREATE TABLE example (id INTEGER)"])
    for _ in range(2):
        assert create_app(app_config).test_client().get("/api/health").json == {
            "status": "ok", "schema": "test_example",
        }

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT MIN(revision) FROM records").fetchone() == (7,)
        assert connection.execute("SELECT * FROM example").fetchall() == []
        rows = connection.execute(
            'SELECT id, migration_file, migration_date, run_on FROM "migration-runs" ORDER BY run_on'
        ).fetchall()
    assert len(rows) == 3
    assert rows[0][1].endswith("-record-migration-runs.py")
    assert rows[2][1:3] == (name, "2026-10-02-12-34-5678")
    for identity, _, _, run_on in rows:
        assert UUID(identity).version == 4
        assert datetime.fromisoformat(run_on).utcoffset().total_seconds() == 0


def test_failed_startup_rolls_back_schema_revision_and_audit(installation):
    _, directory, database, app_config = installation
    create_app(app_config)
    parent = migrations.schema_head()
    add_revision(directory, "2026-11-11-02-00-0000-create-example.py", "test_example", parent,
                 ["CREATE TABLE example (id INTEGER)"])
    add_revision(directory, "2026-11-11-02-01-0000-fail-upgrade.py", "test_failure", "test_example",
                 ["CREATE TABLE partial (id INTEGER)", "INSERT INTO missing VALUES (1)"])

    with pytest.raises(OperationalError, match="no such table: missing"):
        create_app(app_config)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [(parent,)]
        assert connection.execute('SELECT COUNT(*) FROM "migration-runs"').fetchone() == (2,)
        assert connection.execute("SELECT name FROM sqlite_master WHERE name IN ('example', 'partial')").fetchall() == []


def test_managed_api_requires_the_deployment_to_apply_migrations(installation):
    config, _, database, app_config = installation
    command.upgrade(config, "0001_initial")
    app_config["MANAGED_MIGRATIONS"] = True
    with pytest.raises(RuntimeError, match="Run the managed deployment"):
        create_app(app_config)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [("0001_initial",)]
    # The actual deployment command still applies and audits the upgrade.
    command.upgrade(config, "head")
    assert create_app(app_config).test_client().get("/api/health").status_code == 200
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM "migration-runs"').fetchone() == (2,)


@pytest.mark.parametrize("fx", ["1", "1.25"])
def test_remove_returns_preserves_absolute_valuations(installation, fx):
    config, _, database, _ = installation
    command.upgrade(config, "0002_migration_runs")
    rate = Decimal(fx)
    with sqlite3.connect(database) as connection:
        def record(identity, kind, data, deleted=False):
            connection.execute("INSERT INTO records VALUES (?, ?, ?, 1, '2026-01-01T00:00:00Z', ?)",
                               (identity, kind, json.dumps(data), deleted))

        def journal(identity, when, amount, **extra):
            record(identity, "transaction", dict(account_id="portfolio", date=when,
                   amount=amount, usd_amount=str(Decimal(amount) * rate), fx_rate=fx, **extra))
            for suffix, account, native, usd in [
                ("a", "portfolio", Decimal(amount), Decimal(amount) * rate),
                ("b", "counterparty", -Decimal(amount) * rate, -Decimal(amount) * rate),
            ]:
                connection.execute("INSERT INTO postings VALUES (?, ?, ?, ?, ?)",
                                   (identity + suffix, identity, account, str(native), str(usd)))

        record("portfolio", "account", {"name": "Investment"})
        record("counterparty", "account", {"name": "Equity"})
        journal("opening", "2024-01-01", "100", system="opening")
        journal("gain", "2024-01-10", "10", type="Return")
        journal("contribution", "2024-01-15", "20", type="Transfer")
        record("v1", "valuation", {"account_id": "portfolio", "date": "2024-01-31", "amount": "140"})
        journal("j1", "2024-01-31", "10", system="valuation", valuation_id="v1")
        journal("loss", "2024-02-10", "-5", type="Return")
        record("v2", "valuation", {"account_id": "portfolio", "date": "2024-02-28", "amount": "150"})
        journal("j2", "2024-02-28", "15", system="valuation", valuation_id="v2")
        journal("late", "2024-03-01", "3", type="Return")
        record("voided", "transaction", {"date": "2024-01-01", "type": "Return"}, True)
        record("recurring", "subscription", {"type": "Return"})
        record("attachment", "attachment", {"transaction_id": "gain"})
        record("ordinary", "transaction", {"type": "Expense", "subscription_id": "recurring",
                                            "subscription_date": "2024-01-01"})
        connection.execute("INSERT INTO audit_events VALUES ('history', 'gain', 'transaction', 'create', NULL, 'manual', NULL, ?, '2024-01-01')",
                           (json.dumps({"type": "Return"}),))
        valuations_before = connection.execute("SELECT * FROM records WHERE kind='valuation'").fetchall()

    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        remaining = {r[0]: json.loads(r[1]) for r in connection.execute("SELECT id, data FROM records")}
        assert not {"gain", "loss", "late", "voided", "recurring", "attachment"} & remaining.keys()
        assert connection.execute("SELECT * FROM records WHERE kind='valuation'").fetchall() == valuations_before
        assert Decimal(remaining["j1"]["amount"]) == 20
        assert Decimal(remaining["j2"]["amount"]) == 10
        assert remaining["contribution"]["amount"] == "20"
        assert "subscription_id" not in remaining["ordinary"]
        native = [Decimal(r[0]) for r in connection.execute("SELECT native_amount FROM postings WHERE account_id='portfolio'")]
        assert sum(native) == 150
        for identity in ("opening", "contribution", "j1", "j2"):
            assert sum(Decimal(r[0]) for r in connection.execute("SELECT usd_amount FROM postings WHERE transaction_id=?", (identity,))) == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_events WHERE id='history'").fetchone() == (1,)
