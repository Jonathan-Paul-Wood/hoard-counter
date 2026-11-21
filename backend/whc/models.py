"""Storage primitives. Financial quantities are canonical decimal strings, never floats."""
from datetime import datetime, timezone
from uuid import uuid4

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, DDL
from sqlalchemy.engine import Engine

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id():
    return str(uuid4())


@event.listens_for(Engine, "connect")
def sqlite_settings(connection, _):
    if connection.__class__.__module__.startswith("sqlite3"):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


class MigrationRun(db.Model):
    __tablename__ = "migration-runs"
    id = db.Column(db.String(36), primary_key=True)
    migration_file = db.Column(db.String(), nullable=False, unique=True)
    migration_date = db.Column(db.String(21), nullable=False)
    run_on = db.Column(db.String(27), nullable=False)


class Record(db.Model):
    __tablename__ = "records"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    kind = db.Column(db.String(32), nullable=False, index=True)
    data = db.Column(db.JSON, nullable=False, default=dict)
    revision = db.Column(db.Integer, nullable=False, default=1)
    last_modified = db.Column(db.String(32), nullable=False, default=utcnow, index=True)
    deleted = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {**self.data, "id": self.id, "revision": self.revision,
                "last_modified": self.last_modified, "deleted": self.deleted}


# SQLite JSON fields have no VARCHAR length enforcement. Triggers enforce the
# same limits on inserts and updates without rebuilding this referenced table.
RECORD_LIMIT_TRIGGERS = [f"""
CREATE TRIGGER records_text_limits_{operation.lower()} BEFORE {operation} ON records
WHEN (NEW.kind IN ('category', 'tag') AND
      (json_type(NEW.data, '$.name') IS NOT 'text' OR length(json_extract(NEW.data, '$.name')) > 36))
 OR (NEW.kind = 'category' AND json_type(NEW.data, '$.description') IS NOT NULL AND
      (json_type(NEW.data, '$.description') IS NOT 'text' OR length(json_extract(NEW.data, '$.description')) > 120))
BEGIN SELECT RAISE(ABORT, 'Category/tag text exceeds its field limits'); END
""" for operation in ('INSERT', 'UPDATE')]
for statement in RECORD_LIMIT_TRIGGERS:
    event.listen(Record.__table__, 'after_create', DDL(statement).execute_if(dialect='sqlite'))


class Posting(db.Model):
    __tablename__ = "postings"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    transaction_id = db.Column(db.String(36), db.ForeignKey("records.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.String(36), db.ForeignKey("records.id"), nullable=False, index=True)
    native_amount = db.Column(db.String(100), nullable=False)
    usd_amount = db.Column(db.String(100), nullable=False)

    def to_dict(self):
        return {"id": self.id, "transaction_id": self.transaction_id,
                "account_id": self.account_id, "native_amount": self.native_amount,
                "usd_amount": self.usd_amount}


class AuditEvent(db.Model):
    __tablename__ = "audit_events"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    record_id = db.Column(db.String(36), nullable=True, index=True)
    kind = db.Column(db.String(32), nullable=False)
    action = db.Column(db.String(32), nullable=False)
    actor_id = db.Column(db.String(36), nullable=True)
    origin = db.Column(db.String(32), nullable=False, default="manual")
    before = db.Column(db.JSON, nullable=True)
    after = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.String(32), nullable=False, default=utcnow, index=True)

    def to_dict(self):
        return {name: getattr(self, name) for name in
                ("id", "record_id", "kind", "action", "actor_id", "origin", "before", "after", "created_at")}


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.String(36), primary_key=True, default=new_id)
    username = db.Column(db.String(80), nullable=False, unique=True)
    password_hash = db.Column(db.String(512), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="member")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.String(32), nullable=False, default=utcnow)

    def to_dict(self):
        return {"id": self.id, "username": self.username, "role": self.role,
                "active": self.active, "created_at": self.created_at}
