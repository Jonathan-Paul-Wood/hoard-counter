"""Shared Alembic configuration for startup and managed deployments."""
from datetime import datetime
from pathlib import Path
import re

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from .models import new_id, utcnow

BACKEND = Path(__file__).resolve().parents[1]
LEGACY_REVISION = "0001_initial"
FILENAME = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.py", re.ASCII)


def migration_config():
    return Config(str(BACKEND / "alembic.ini"))


def schema_head():
    head = ScriptDirectory.from_config(migration_config()).get_current_head()
    if head is None:
        raise RuntimeError("No database migrations were found.")
    return head


def prepare_database(engine, *, managed=False):
    config = migration_config()
    config.attributes["engine"] = engine
    config.attributes["require_current"] = managed
    command.upgrade(config, "head")


def migration_files(scripts):
    """Validate revision filenames before running any upgrade."""
    files = {}
    for script in scripts.walk_revisions():
        if script.revision == LEGACY_REVISION:
            continue
        name = Path(script.path).name
        match = FILENAME.fullmatch(name)
        if match:
            date = match[1]
            try:
                datetime.strptime(date[:-2], "%Y-%m-%d-%H-%M-%S")
            except ValueError:
                pass
            else:
                files[script.revision] = (name, date)
                continue
        raise RuntimeError(f"Invalid migration filename: {name}. Expected yyyy-mm-dd-hh-mm-ssss-slug.py.")
    return files


def audit_upgrade(files):
    def record(ctx, step, **_):
        if not step.is_migration or not step.is_upgrade or step.up_revision_id == LEGACY_REVISION:
            return
        name, date = files[step.up_revision_id]
        ctx.connection.execute(
            text('INSERT INTO "migration-runs" (id, migration_file, migration_date, run_on) '
                 'VALUES (:id, :name, :date, :run_on)'),
            dict(id=new_id(), name=name, date=date, run_on=utcnow()),
        )

    return record
