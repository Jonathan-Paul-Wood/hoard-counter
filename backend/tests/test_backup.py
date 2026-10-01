from datetime import datetime, timezone
from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import threading

import pytest

from whc.backup import create_backup, restore_backup, verify_backup, retention_keep, prune_backups, mutation_lock


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def state(tmp_path):
    data = tmp_path / "data"
    uploads = tmp_path / "uploads"
    backups = tmp_path / "backups"
    data.mkdir()
    uploads.mkdir()
    database = data / "hoardcounter.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.executescript("""
          PRAGMA journal_mode=WAL;
          PRAGMA foreign_keys=ON;
          CREATE TABLE accounts(id TEXT PRIMARY KEY);
          CREATE TABLE postings(id INTEGER PRIMARY KEY, account TEXT REFERENCES accounts(id), amount TEXT);
          INSERT INTO accounts VALUES ('checking'), ('groceries');
          INSERT INTO postings VALUES (1, 'checking', '-125.50'), (2, 'groceries', '125.50');
          CREATE TABLE alembic_version(version_num TEXT);
          INSERT INTO alembic_version VALUES ('0001_initial');
        """)
    (uploads / "receipt.pdf").write_bytes(b"receipt fixture")
    (uploads / "photo.jpg").write_bytes(b"photo fixture")
    return database, uploads, backups


def test_snapshot_restore_includes_database_uploads_and_manifest(state, tmp_path):
    database, uploads, backups = state
    archive = create_backup(database, uploads, backups)
    manifest = verify_backup(archive)
    assert manifest["schema_revision"] == "0001_initial"
    assert set(manifest["files"]) == {"database.sqlite3", "uploads/receipt.pdf", "uploads/photo.jpg"}
    restore_backup(archive, tmp_path / "restored")
    with sqlite3.connect(tmp_path / "restored/data/hoardcounter.db") as conn:
        assert conn.execute("SELECT sum(CAST(amount AS NUMERIC)) FROM postings").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM postings").fetchone()[0] == 2
    assert (tmp_path / "restored/uploads/receipt.pdf").read_bytes() == b"receipt fixture"
    with pytest.raises(ValueError, match="fresh directory"):
        restore_backup(archive, tmp_path / "restored")


def test_live_wal_records_are_included(state):
    database, uploads, backups = state
    # Hold a connection open so this committed row can remain in the WAL.
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO accounts VALUES ('savings')")
        conn.commit()
        archive = create_backup(database, uploads, backups)
        destination = backups.parent / "restored"
        restore_backup(archive, destination)
        with sqlite3.connect(destination / "data/hoardcounter.db") as restored:
            assert restored.execute("SELECT count(*) FROM accounts").fetchone()[0] == 3


def test_failed_creation_never_publishes_partial_archive(state, monkeypatch):
    database, uploads, backups = state
    good = create_backup(database, uploads, backups)
    import whc.backup as backup
    monkeypatch.setattr(backup, "verify_backup", lambda _: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        create_backup(database, uploads, backups)
    assert list(backups.iterdir()) == [good]


def test_checksum_failure_leaves_restore_target_absent(state, tmp_path):
    database, uploads, backups = state
    archive = create_backup(database, uploads, backups)
    corrupt = tmp_path / "corrupt.tar.gz"
    with tarfile.open(archive) as source, tarfile.open(corrupt, "w:gz") as dest:
        for member in source.getmembers():
            content = source.extractfile(member).read()
            if member.name == "uploads/photo.jpg":
                content = b"X" * len(content)
            dest.addfile(member, io.BytesIO(content))
    with pytest.raises(ValueError, match="checksum"):
        restore_backup(corrupt, tmp_path / "failed-restore")
    assert not (tmp_path / "failed-restore").exists()


def test_traversal_archive_is_rejected(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        item = tarfile.TarInfo("../escape")
        item.size = 1
        output.addfile(item, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="Unsafe path"):
        verify_backup(archive)
    assert not (tmp_path / "escape").exists()


def manifest(ident, when, purpose="scheduled"):
    return {"id": ident, "created_at": when, "purpose": purpose}


def test_retention_calendar_union_and_in_progress_annual_candidate():
    rows = [
        manifest("2023-year", "2023-12-31T12:00:00Z"),
        manifest("2024-year", "2024-12-29T12:00:00Z"),
        manifest("q1", "2025-03-30T12:00:00Z"),
        manifest("q2", "2025-06-29T12:00:00Z"),
        manifest("q3", "2025-09-28T12:00:00Z"),
        manifest("oct-old", "2025-10-05T12:00:00Z"),
        manifest("oct", "2025-10-26T12:00:00Z"),
        manifest("nov", "2025-11-30T12:00:00Z"),
        manifest("dec-old", "2025-12-07T12:00:00Z"),
        manifest("dec14", "2025-12-14T12:00:00Z"),
        manifest("dec21", "2025-12-21T12:00:00Z"),
        manifest("dec28", "2025-12-28T12:00:00Z"),
        manifest("jan04", "2026-01-04T12:00:00Z"),
    ]
    kept = retention_keep(rows, now=stamp("2026-01-04T13:00:00Z"))
    assert kept == {"2023-year", "2024-year", "q2", "q3", "oct", "nov", "dec14", "dec21", "dec28", "jan04"}
    # At an outage, keep the annual candidate even after weekly/monthly windows expire.
    kept = retention_keep([manifest("candidate", "2026-01-04T12:00:00Z")],
                          now=stamp("2026-12-15T12:00:00Z"))
    assert kept == {"candidate"}


def test_same_week_latest_only_leap_day_and_manual_recovery():
    rows = [manifest("older", "2024-02-25T12:00:00Z"),
            manifest("leap", "2024-02-29T12:00:00Z"),
            manifest("newer", "2024-03-01T12:00:00Z"),
            manifest("rollback", "2023-01-01T12:00:00Z", "pre-upgrade")]
    assert retention_keep(rows, now=stamp("2024-03-02T12:00:00Z")) == {
        "older", "leap", "newer", "rollback"}
    # Same ISO week retains the newer snapshot, but leap day remains February representative.
    assert retention_keep(rows[:1] + rows[2:], now=stamp("2024-03-02T12:00:00Z")) == {"older", "newer", "rollback"}


def test_pruning_verifies_all_archives_before_deleting_any(state):
    database, uploads, backups = state
    old = create_backup(database, uploads, backups, now=stamp("2024-01-01T12:00:00Z"))
    new = create_backup(database, uploads, backups, now=stamp("2024-12-31T12:00:00Z"))
    broken = backups / "broken.tar.gz"
    broken.write_bytes(b"not gzip")
    with pytest.raises(tarfile.ReadError):
        prune_backups(backups, now=stamp("2025-01-01T12:00:00Z"))
    assert old.exists() and new.exists()
    broken.unlink()
    assert prune_backups(backups, now=stamp("2025-01-01T12:00:00Z"), dry_run=True) == [str(old)]
    assert old.exists()
    prune_backups(backups, now=stamp("2025-01-01T12:00:00Z"))
    assert not old.exists() and new.exists()


def test_backup_gate_blocks_mutations_and_releases_after_exception(tmp_path):
    attempted, acquired = threading.Event(), threading.Event()

    def writer():
        attempted.set()
        with mutation_lock(tmp_path):
            acquired.set()

    with pytest.raises(RuntimeError, match="simulated"):
        with mutation_lock(tmp_path, exclusive=True):
            worker = threading.Thread(target=writer)
            worker.start()
            assert attempted.wait(1)
            assert not acquired.wait(0.1)
            raise RuntimeError("simulated backup failure")
    worker.join(timeout=3)
    assert acquired.is_set() and not worker.is_alive()


def test_failed_migration_restores_previous_data_and_release(state, monkeypatch):
    database, uploads, backups = state
    root = database.parent.parent
    spec = importlib.util.spec_from_file_location("deployment_manager", Path(__file__).resolve().parents[2] / "scripts/manage.py")
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    previous = {"version": "0.1.0", "rollback_backup": None}
    manager.write_release(root, previous)
    monkeypatch.setattr(manager.subprocess, "run", lambda *a, **kw: type("Result", (), {"returncode": 1})())

    def fake_compose(env, release, *args, **kwargs):
        if "alembic" in args:
            with closing(sqlite3.connect(database)) as conn:
                conn.execute("UPDATE postings SET amount='999' WHERE id=1")
                conn.commit()
            raise RuntimeError("simulated migration failure")

    def actual_backup(env, data_root, release, purpose, **kwargs):
        return create_backup(database, uploads, backups, purpose=purpose)

    def actual_stage(env, data_root, release, archive):
        stage = root / ".restore-test"
        stage.mkdir()
        restore_backup(archive, stage / "restored")
        return stage

    started = []
    monkeypatch.setattr(manager, "compose", fake_compose)
    monkeypatch.setattr(manager, "backup", actual_backup)
    monkeypatch.setattr(manager, "stage_restore", actual_stage)
    monkeypatch.setattr(manager, "start_apps", lambda env, release, **kw: started.append(release))
    with pytest.raises(RuntimeError, match="simulated migration"):
        manager.deploy({}, root, "0.2.0")
    assert started == ["0.1.0"]
    assert manager.read_release(root) == previous
    assert not (root / "data/.maintenance").exists()
    assert (uploads / "receipt.pdf").read_bytes() == b"receipt fixture"
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT sum(CAST(amount AS NUMERIC)) FROM postings").fetchone()[0] == 0


def test_scheduler_and_retention_use_saved_household_timezone(state, monkeypatch):
    from whc.backup import configured_timezone
    database, _, _ = state
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("CREATE TABLE records(kind TEXT, deleted INTEGER, data TEXT)")
        conn.execute("INSERT INTO records VALUES ('setting', 0, ?)", (json.dumps({'timezone': 'Asia/Tokyo'}),))
        conn.commit()
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(database))
    monkeypatch.setenv('HOUSEHOLD_TIMEZONE', 'America/New_York')
    assert configured_timezone() == 'Asia/Tokyo'
    spec = importlib.util.spec_from_file_location("timezone_manager", Path(__file__).resolve().parents[2] / "scripts/manage.py")
    manager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manager)
    assert manager.household_timezone({'HOUSEHOLD_TIMEZONE': 'America/New_York'}, database.parent.parent).key == 'Asia/Tokyo'
