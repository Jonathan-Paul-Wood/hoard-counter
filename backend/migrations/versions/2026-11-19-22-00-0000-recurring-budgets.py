"""Preserve existing monthly budgets as bounded allocation schedules."""
import calendar
from datetime import date

from alembic import op
import sqlalchemy as sa

revision = "0003_recurring_budgets"
down_revision = "0002_migration_runs"
branch_labels = None
depends_on = None


def upgrade():
    records = sa.table('records', sa.column('id', sa.String),
                       sa.column('kind', sa.String), sa.column('data', sa.JSON))
    connection = op.get_bind()
    for row in connection.execute(sa.select(records).where(records.c.kind == 'budget')).mappings():
        data = dict(row['data'])
        if 'month' not in data:
            continue
        start = date.fromisoformat(data.pop('month') + '-01')
        data.update(start_date=start.isoformat(), end_date=start.replace(
            day=calendar.monthrange(start.year, start.month)[1]).isoformat(), frequency='monthly')
        connection.execute(records.update().where(records.c.id == row['id']).values(data=data))


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled. Restore a verified pre-upgrade backup instead.")
