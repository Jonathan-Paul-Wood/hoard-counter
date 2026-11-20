"""Remove Return transactions and expectations, preserving valuation targets.

Returns share the records table with other entities; there is no Return table.
Keep audit history and immutable upload bytes, as with ordinary ledger removals.
"""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "0003_remove_returns"
down_revision = "0002_migration_runs"
branch_labels = None
depends_on = None


def upgrade():
    with localcontext() as context:
        context.prec = 100
        _remove_returns(op.get_bind())


def _remove_returns(connection):
    metadata = sa.MetaData()
    records = sa.Table("records", metadata, autoload_with=connection)
    postings = sa.Table("postings", metadata, autoload_with=connection)
    audits = sa.Table("audit_events", metadata, autoload_with=connection)
    rows = list(connection.execute(sa.select(records)).mappings())
    removed = {r["id"] for r in rows if r["kind"] in ("transaction", "subscription")
               and r["data"].get("type") == "Return"}
    if not removed:
        return
    removed_subscriptions = {r["id"] for r in rows if r["id"] in removed and r["kind"] == "subscription"}
    removed_postings = list(connection.execute(sa.select(postings).where(
        postings.c.transaction_id.in_(removed))).mappings())
    dates = {r["id"]: r["data"].get("date") for r in rows if r["id"] in removed}
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def update(row, data):
        before = {**row["data"], **{k: row[k] for k in ("id", "revision", "last_modified", "deleted")}}
        after = {**data, "id": row["id"], "revision": row["revision"] + 1,
                 "last_modified": now, "deleted": row["deleted"]}
        connection.execute(records.update().where(records.c.id == row["id"]).values(
            data=data, revision=after["revision"], last_modified=now))
        connection.execute(audits.insert().values(
            id=str(uuid4()), record_id=row["id"], kind=row["kind"], action="update",
            actor_id=None, origin="migration", before=before, after=after, created_at=now))

    # Each absolute valuation absorbs the Returns since the preceding valuation.
    # Change only derived journals, never the user's saved Valuation entries.
    previous = {}
    journals = sorted((r for r in rows if r["kind"] == "transaction" and not r["deleted"]
                       and r["data"].get("system") == "valuation"), key=lambda r: r["data"]["date"])
    for journal in journals:
        data = dict(journal["data"])
        account, when = data["account_id"], data["date"]
        delta = sum((Decimal(p["native_amount"]) for p in removed_postings
                     if p["account_id"] == account and dates[p["transaction_id"]] <= when
                     and (account not in previous or dates[p["transaction_id"]] > previous[account])), Decimal(0))
        previous[account] = when
        if not delta:
            continue
        usd_delta = delta * Decimal(data["fx_rate"])
        data["amount"] = format(Decimal(data["amount"]) + delta, "f")
        data["usd_amount"] = format(Decimal(data["usd_amount"]) + usd_delta, "f")
        update(journal, data)
        for posting in connection.execute(sa.select(postings).where(
                postings.c.transaction_id == journal["id"])).mappings():
            native_change = delta if posting["account_id"] == account else -usd_delta
            usd_change = usd_delta if posting["account_id"] == account else -usd_delta
            connection.execute(postings.update().where(postings.c.id == posting["id"]).values(
                native_amount=format(Decimal(posting["native_amount"]) + native_change, "f"),
                usd_amount=format(Decimal(posting["usd_amount"]) + usd_change, "f")))

    for row in rows:
        if row["id"] in removed:
            continue
        if row["kind"] == "attachment" and row["data"].get("transaction_id") in removed:
            removed.add(row["id"])
        elif row["kind"] == "transaction" and row["data"].get("subscription_id") in removed_subscriptions:
            data = {k: v for k, v in row["data"].items()
                    if k not in ("subscription_id", "subscription_date", "subscription_candidates")}
            update(row, data)
    connection.execute(postings.delete().where(postings.c.transaction_id.in_(removed)))
    connection.execute(records.delete().where(records.c.id.in_(removed)))


def downgrade():
    raise RuntimeError("Deleted Returns cannot be recovered. Restore a verified pre-upgrade backup instead.")
