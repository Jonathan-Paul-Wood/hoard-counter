"""Limit category/tag text without rebuilding the ledger's referenced table."""
from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = '0006_category_text_limits'
down_revision = '0005_merge_returns_budgets'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    metadata = sa.MetaData()
    records = sa.Table('records', metadata, autoload_with=connection)
    audits = sa.Table('audit_events', metadata, autoload_with=connection)
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    for row in connection.execute(sa.select(records).where(records.c.kind.in_(['category', 'tag']))).mappings():
        data = dict(row['data'])
        data['name'] = data['name'][:36]
        if row['kind'] == 'category' and 'description' in data:
            data['description'] = (data.get('description') or '')[:120]
        if data == row['data']:
            continue
        before = {**row['data'], **{key: row[key] for key in ('id', 'revision', 'last_modified', 'deleted')}}
        after = {**data, 'id': row['id'], 'revision': row['revision'] + 1, 'last_modified': now, 'deleted': row['deleted']}
        connection.execute(records.update().where(records.c.id == row['id']).values(
            data=data, revision=after['revision'], last_modified=now))
        connection.execute(audits.insert().values(id=str(uuid4()), record_id=row['id'], kind=row['kind'],
            action='update', actor_id=None, origin='migration', before=before, after=after, created_at=now))
    # Keep migration SQL immutable, independent of future model changes.
    for operation in ('INSERT', 'UPDATE'):
        connection.exec_driver_sql(f"""CREATE TRIGGER records_text_limits_{operation.lower()} BEFORE {operation} ON records
WHEN (NEW.kind IN ('category', 'tag') AND
      (json_type(NEW.data, '$.name') IS NOT 'text' OR length(json_extract(NEW.data, '$.name')) > 36))
 OR (NEW.kind = 'category' AND json_type(NEW.data, '$.description') IS NOT NULL AND
      (json_type(NEW.data, '$.description') IS NOT 'text' OR length(json_extract(NEW.data, '$.description')) > 120))
BEGIN SELECT RAISE(ABORT, 'Category/tag text exceeds its field limits'); END""")


def downgrade():
    raise RuntimeError('Restore a verified pre-upgrade backup to recover truncated names.')
