"""Reset budget records for the period/frequency model."""
from alembic import op
import sqlalchemy as sa

revision = "0004_reset_budgets"
down_revision = "0003_recurring_budgets"
branch_labels = None
depends_on = None


def upgrade():
    records = sa.table('records', sa.column('id', sa.String), sa.column('kind', sa.String))
    audit = sa.table('audit_events', sa.column('kind', sa.String), sa.column('record_id', sa.String))
    connection = op.get_bind()
    connection.execute(audit.delete().where(sa.or_(audit.c.kind == 'budget',
        audit.c.record_id.in_(sa.select(records.c.id).where(records.c.kind == 'budget')))))
    connection.execute(records.delete().where(records.c.kind == 'budget'))


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled. Restore a verified pre-upgrade backup instead.")
