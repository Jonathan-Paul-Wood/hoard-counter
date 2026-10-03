"""One-shot Docker bootstrap; existing installations require managed upgrades."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from . import SCHEMA_REVISION
from .backup import mutation_lock

UID = 10001
BACKEND = Path(__file__).resolve().parents[1]


@contextmanager
def operations_lock(root):
    if os.name != 'posix':
        # Enables isolated bootstrap tests on Windows; deployed containers use flock.
        with mutation_lock(root, exclusive=True):
            yield
        return
    import fcntl
    with (root / '.operations.lock').open('a+b') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another backup, restore, or deployment is running.') from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def schema(database):
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
            revisions = connection.execute('SELECT version_num FROM alembic_version').fetchall()
            connection.execute('SELECT 1 FROM records LIMIT 1')
        return revisions[0][0] if len(revisions) == 1 else None
    except sqlite3.DatabaseError:
        return None
    finally:
        # Even a read-only WAL connection can create -wal/-shm sidecars. The
        # root initializer must leave those writable by the non-root API.
        if os.name == 'posix' and os.geteuid() == 0:
            for suffix in ('', '-wal', '-shm'):
                path = database.with_name(database.name + suffix)
                if path.is_symlink():
                    raise RuntimeError('SQLite files must not be symlinks.')
                if path.exists():
                    os.chown(path, UID, UID)


def migrate(data):
    env = dict(os.environ, DATA_DIR=str(data), DATABASE_URL='sqlite:///' + (data / 'hoardcounter.db').as_posix())
    privileges = dict(user=UID, group=UID, extra_groups=[]) if os.name == 'posix' and os.geteuid() == 0 else {}
    subprocess.run([sys.executable, '-m', 'alembic', '-c', str(BACKEND / 'alembic.ini'), 'upgrade', 'head'],
                   cwd=BACKEND, env=env, check=True, **privileges)


def initialize(root, release):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', release) or release in ('latest', 'development'):
        raise RuntimeError('Set APP_VERSION to an immutable release tag.')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with operations_lock(root):
        for name in ('data', 'uploads', 'backups'):
            directory = root / name
            if directory.is_symlink():
                raise RuntimeError('Persistent directories must not be symlinks.')
            directory.mkdir(exist_ok=True)
            if os.name == 'posix' and os.geteuid() == 0:
                os.chown(directory, UID, UID)
            directory.chmod(0o755 if name == 'data' else 0o750)
        database = root / 'data' / 'hoardcounter.db'
        state_path = root / 'release.json'
        if database.is_symlink() or state_path.is_symlink():
            raise RuntimeError('Database and release metadata must not be symlinks.')
        if (root / 'data' / '.maintenance').exists():
            raise RuntimeError('Maintenance is active; complete the pending deployment or recovery first.')
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state.get('version') != release:
                raise RuntimeError('A different release is installed. Use scripts/manage.py deploy for a backed-up upgrade.')
            if not database.is_file() or schema(database) != SCHEMA_REVISION:
                raise RuntimeError('Installed schema does not match this image. Use managed deployment or restore.')
            return
        if database.exists():
            raise RuntimeError('Existing untracked database: restore its matching release; bootstrap will not modify it.')
        migrate(root / 'data')
        if schema(database) != SCHEMA_REVISION:
            raise RuntimeError('Initialization did not produce the expected schema.')
        temporary = root / '.release.json.partial'
        temporary.write_text(json.dumps(dict(version=release, previous_version=None, rollback_backup=None), indent=2) + '\n')
        os.replace(temporary, state_path)


def main():
    secret = os.environ.get('SECRET_KEY', '')
    if len(secret) < 32 or 'replace-with' in secret:
        raise RuntimeError('Set SECRET_KEY to a random secret of at least 32 characters in .env.')
    initialize(Path('/state'), os.environ.get('APP_VERSION', ''))
    print('Storage and schema ready. Open the configured Docker HTTP port.')


if __name__ == '__main__':
    main()
