"""Record each successful migration from this revision forward."""
from alembic import op
import sqlalchemy as sa

revision = "0002_migration_runs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "migration-runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("migration_file", sa.String(), nullable=False, unique=True),
        sa.Column("migration_date", sa.String(21), nullable=False),
        sa.Column("run_on", sa.String(27), nullable=False),
    )


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled. Restore a verified pre-upgrade backup instead.")
