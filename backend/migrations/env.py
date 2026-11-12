import os
from pathlib import Path
from alembic import context
from sqlalchemy import create_engine, pool
from whc.migrations import audit_upgrade, migration_files
from whc.models import db

config = context.config
if context.is_offline_mode():
    raise RuntimeError("Run migrations against the database so successful runs can be audited.")

files = migration_files(context.script)
engine = config.attributes.get("engine")
if engine is None:
    data_dir = Path(os.environ.get("DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    url = os.environ.get("DATABASE_URL", "sqlite:///" + data_dir.joinpath("hoardcounter.db").as_posix())
    engine = create_engine(url, poolclass=pool.NullPool)

with engine.connect() as connection:
    with connection.begin():
        if connection.dialect.name == "sqlite":
            # Lock before reading the revision, and make SQLite DDL transactional.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        context.configure(connection=connection, target_metadata=db.metadata, render_as_batch=True,
                          on_version_apply=audit_upgrade(files))
        if config.attributes.get("require_current"):
            current = context.get_context().get_current_heads()
            if current != (context.script.get_current_head(),):
                raise RuntimeError("Database schema is not current. Run the managed deployment to upgrade it.")
        with context.begin_transaction():
            context.run_migrations()
