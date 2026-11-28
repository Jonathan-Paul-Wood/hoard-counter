from datetime import datetime
from decimal import Decimal
import json
import shutil
import sqlite3
from uuid import UUID

from alembic import command
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy.exc import OperationalError

from whc import create_app, migrations


def test_migration_directory_has_exactly_one_head():
    scripts = ScriptDirectory.from_config(migrations.migration_config())
    assert scripts.get_heads() == ["0007_pending_transactions"]
    assert scripts.get_revision("head").down_revision == "0006_category_text_limits"
    assert set(scripts.get_revision("0005_merge_returns_budgets").down_revision) == {
        "0003_remove_returns", "0004_reset_budgets",
    }
    migrations.migration_files(scripts)


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
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [(migrations.schema_head(),)]


def test_text_limit_migration_preserves_ids_references_and_audits_truncation(installation):
    config, _, database, _ = installation
    command.upgrade(config, '0005_merge_returns_budgets')
    with sqlite3.connect(database) as connection:
        for identity, kind, deleted in [('category', 'category', 0), ('tag', 'tag', 1)]:
            connection.execute('INSERT INTO records VALUES (?, ?, ?, 7, ?, ?)',
                (identity, kind, json.dumps({'name': 'x' * 45}), '2025-01-01T00:00:00Z', deleted))
        connection.execute('INSERT INTO records VALUES (?, ?, ?, 1, ?, 0)',
            ('reference', 'rule', json.dumps({'name': 'Rule', 'actions': {'category_id': 'category', 'tag_ids': ['tag']}}), '2025-01-01T00:00:00Z'))
        for identity, kind in [('financial', 'account'), ('journal', 'transaction')]:
            connection.execute('INSERT INTO records VALUES (?, ?, ?, 1, ?, 0)',
                (identity, kind, '{}', '2025-01-01T00:00:00Z'))
        connection.execute('INSERT INTO postings VALUES (?, ?, ?, ?, ?)', ('posting', 'journal', 'financial', '10', '10'))
        old_postings = connection.execute('SELECT * FROM postings').fetchall()
    command.upgrade(config, 'head')
    with sqlite3.connect(database) as connection:
        for identity in ('category', 'tag'):
            data, revision = connection.execute('SELECT data, revision FROM records WHERE id = ?', (identity,)).fetchone()
            assert json.loads(data)['name'] == 'x' * 36 and revision == 8
            before, after = connection.execute('SELECT before, after FROM audit_events WHERE record_id = ?', (identity,)).fetchone()
            assert json.loads(before)['name'] == 'x' * 45
            assert json.loads(after)['name'] == 'x' * 36
        assert connection.execute('SELECT deleted FROM records WHERE id = ?', ('tag',)).fetchone() == (1,)
        assert json.loads(connection.execute('SELECT data FROM records WHERE id = ?', ('reference',)).fetchone()[0])['actions']['category_id'] == 'category'
        assert connection.execute('SELECT * FROM postings').fetchall() == old_postings
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute('UPDATE records SET data = ? WHERE id = ?', (json.dumps({'name': 'long' * 10}), 'tag'))


@pytest.mark.parametrize("starting_revisions", [
    (),
    ("0002_migration_runs",),
    ("0003_remove_returns",),
    ("0003_recurring_budgets",),
    ("0004_reset_budgets",),
    ("0003_remove_returns", "0004_reset_budgets"),
], ids=["fresh", "common-ancestor", "returns-head", "recurring-budgets", "budgets-head", "both-heads"])
def test_merge_upgrades_all_branch_states_and_audits_once(installation, starting_revisions):
    config, _, database, app_config = installation
    scripts = ScriptDirectory.from_config(config)
    for revision in starting_revisions:
        command.upgrade(config, revision)
    if starting_revisions:
        expected_start = {starting_revisions[-1]} if len(starting_revisions) == 1 else set(starting_revisions)
        with sqlite3.connect(database) as connection:
            assert {row[0] for row in connection.execute("SELECT version_num FROM alembic_version")} == expected_start
            previous_audits = connection.execute('SELECT * FROM "migration-runs" ORDER BY id').fetchall()
    else:
        previous_audits = []

    expected_files = {name for name, _ in migrations.migration_files(scripts).values()}
    snapshots = []
    for _ in range(2):
        app = create_app(app_config)
        assert app.test_client().get("/api/health").json == {
            "status": "ok", "schema": "0007_pending_transactions",
        }
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [("0007_pending_transactions",)]
            audits = connection.execute('SELECT * FROM "migration-runs" ORDER BY id').fetchall()
            names = connection.execute('SELECT migration_file FROM "migration-runs"').fetchall()
            assert len(names) == len(expected_files)
            assert {row[0] for row in names} == expected_files
            assert all(row in audits for row in previous_audits)
            snapshots.append((audits, connection.execute("SELECT * FROM records ORDER BY id").fetchall()))
    assert snapshots[0] == snapshots[1]
