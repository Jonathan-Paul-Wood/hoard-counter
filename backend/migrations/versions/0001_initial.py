"""Initial exact-decimal ledger and household users."""
from uuid import NAMESPACE_URL, uuid5
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    records = op.create_table("records",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("data", sa.JSON, nullable=False), sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("last_modified", sa.String(32), nullable=False), sa.Column("deleted", sa.Boolean, nullable=False))
    op.create_index("ix_records_kind", "records", ["kind"])
    op.create_index("ix_records_last_modified", "records", ["last_modified"])
    op.create_table("postings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("transaction_id", sa.String(36), sa.ForeignKey("records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("records.id"), nullable=False),
        sa.Column("native_amount", sa.String(100), nullable=False), sa.Column("usd_amount", sa.String(100), nullable=False))
    op.create_index("ix_postings_transaction_id", "postings", ["transaction_id"])
    op.create_index("ix_postings_account_id", "postings", ["account_id"])
    op.create_table("audit_events",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("record_id", sa.String(36)),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(36)), sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("before", sa.JSON), sa.Column("after", sa.JSON), sa.Column("created_at", sa.String(32), nullable=False))
    op.create_index("ix_audit_events_record_id", "audit_events", ["record_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_table("users", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(512), nullable=False), sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False), sa.Column("created_at", sa.String(32), nullable=False))
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    values = []
    for key, name, cls, typ in [("opening", "Opening balances", "equity", "Equity"),
                              ("valuation", "Market valuation gains and losses", "equity", "Equity"),
                              ("fx", "Currency conversion gains and losses", "equity", "Equity"),
                              ("reconciliation", "Balance reconciliation", "equity", "Equity"),
                              ("income", "Income", "income", "Income Source"), ("expense", "Expenses", "expense", "Expense Payee")]:
        values.append(dict(id=str(uuid5(NAMESPACE_URL, "hoard-counter/system/" + key)), kind="account",
                           data={"name": name, "type": typ, "account_class": cls, "currency": "USD", "system": key,
                                 "opening_balance": "0", "opening_date": "1970-01-01"},
                           revision=1, last_modified=now, deleted=False))
    op.bulk_insert(records, values)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled. Restore a verified pre-upgrade backup instead.")
