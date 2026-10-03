"""Docker bootstrap data safety and local HTTP session regression checks."""
from http.cookies import SimpleCookie
import json
import sqlite3

from alembic import command
from alembic.config import Config
import pytest

from whc import create_app, SCHEMA_REVISION
from whc import initialize as startup
from whc.models import db


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / 'household'

    def migrate(data):
        monkeypatch.setenv('DATA_DIR', str(data))
        monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + (data / 'hoardcounter.db').as_posix())
        command.upgrade(Config(str(startup.BACKEND / 'alembic.ini')), 'head')

    # Real Alembic migration, without requiring Linux UID switching in unit tests.
    monkeypatch.setattr(startup, 'migrate', migrate)
    return root


def test_bootstrap_initializes_once_and_preserves_existing_records(storage, monkeypatch):
    startup.initialize(storage, '1.0.0')
    database = storage / 'data/hoardcounter.db'
    assert startup.schema(database) == SCHEMA_REVISION
    assert json.loads((storage / 'release.json').read_text())['version'] == '1.0.0'
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE records SET revision=7 WHERE kind='account'")
    receipt = storage / 'uploads/receipt.pdf'
    receipt.write_bytes(b'receipt kept across restarts')
    monkeypatch.setattr(startup, 'migrate', lambda _: pytest.fail('Existing databases must not be migrated during startup'))
    startup.initialize(storage, '1.0.0')
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT min(revision) FROM records').fetchone()[0] == 7
    assert receipt.read_bytes() == b'receipt kept across restarts'


@pytest.mark.parametrize('condition', ['different-release', 'different-schema', 'maintenance', 'untracked'])
def test_bootstrap_rejects_unsafe_existing_installation(storage, condition):
    startup.initialize(storage, '1.0.0')
    database = storage / 'data/hoardcounter.db'
    release = '1.0.0'
    if condition == 'different-release':
        release = '2.0.0'
    elif condition == 'different-schema':
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE alembic_version SET version_num='future_schema'")
    elif condition == 'maintenance':
        (storage / 'data/.maintenance').write_text('Upgrade in progress')
    else:
        (storage / 'release.json').unlink()
    before = database.read_bytes()
    with pytest.raises(RuntimeError):
        startup.initialize(storage, release)
    assert database.read_bytes() == before


def test_failed_bootstrap_does_not_publish_release_or_retry_over_partial_data(storage, monkeypatch):
    def failing_migration(data):
        (data / 'hoardcounter.db').write_bytes(b'partial migration')
        raise RuntimeError('migration failed')
    monkeypatch.setattr(startup, 'migrate', failing_migration)
    with pytest.raises(RuntimeError, match='migration failed'):
        startup.initialize(storage, '1.0.0')
    assert not (storage / 'release.json').exists()
    with pytest.raises(RuntimeError, match='untracked'):
        startup.initialize(storage, '1.0.0')


def test_migration_runs_as_application_user(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(startup.subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(startup.os, 'name', 'posix')
    monkeypatch.setattr(startup.os, 'geteuid', lambda: 0, raising=False)
    startup.migrate(tmp_path)
    _, options = calls[0]
    assert options['user'] == options['group'] == 10001
    assert options['extra_groups'] == []
    assert options['env']['DATABASE_URL'].endswith('/hoardcounter.db')


@pytest.mark.parametrize('https', [False, True])
def test_direct_http_and_optional_https_sessions(tmp_path, monkeypatch, https):
    if https:
        monkeypatch.setenv('COOKIE_SECURE', 'true')
    else:
        monkeypatch.delenv('COOKIE_SECURE', raising=False)
    app = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path),
                      'UPLOAD_DIR': str(tmp_path / 'uploads'), 'BACKUP_DIR': str(tmp_path / 'backups'),
                      'DATABASE_URL': 'sqlite:///:memory:'})
    client = app.test_client()
    origin = 'https://household.example' if https else 'http://localhost:8080'
    credentials = {'username': 'owner', 'password': 'correct horse battery staple'}
    setup = client.post('/api/v1/auth/setup', json=credentials, base_url=origin, headers={'Origin': origin})
    assert setup.status_code == 201
    cookie = SimpleCookie(setup.headers['Set-Cookie'])['session']
    assert bool(cookie['secure']) == https
    assert cookie['httponly'] and cookie['samesite'] == 'Lax'
    session = client.get('/api/v1/auth/session', base_url=origin).json
    assert session['user']['username'] == 'owner'
    headers = {'Origin': origin, 'X-CSRF-Token': session['csrf_token']}
    assert client.post('/api/v1/categories', json={'name': 'Household'}, base_url=origin, headers=headers).status_code == 201
    assert client.post('/api/v1/categories', json={'name': 'Missing CSRF'}, base_url=origin).status_code == 403
    assert client.post('/api/v1/categories', json={'name': 'Wrong origin'}, base_url=origin,
                       headers={**headers, 'Origin': 'http://other.example'}).status_code == 403
    assert client.post('/api/v1/auth/logout', base_url=origin, headers=headers).status_code == 200
    assert client.post('/api/v1/auth/login', json=credentials, base_url=origin, headers={'Origin': origin}).status_code == 200
    assert client.get('/api/v1/categories', base_url=origin).json['total'] == 1
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
