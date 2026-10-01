import os
from pathlib import Path
from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine, pool
from whc.models import db

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
data_dir = Path(os.environ.get("DATA_DIR", "./data")).resolve()
data_dir.mkdir(parents=True, exist_ok=True)
url = os.environ.get("DATABASE_URL", "sqlite:///" + str(data_dir / "hoardcounter.db").replace("\\", "/"))
target_metadata = db.metadata

if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
