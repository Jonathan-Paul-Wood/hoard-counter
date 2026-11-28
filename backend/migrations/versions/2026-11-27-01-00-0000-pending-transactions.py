"""Persistent transaction import drafts."""
from alembic import op
import sqlalchemy as sa

revision = '0007_pending_transactions'
down_revision = '0006_category_text_limits'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pending_transactions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('submission_id', sa.String(36), nullable=False),
        sa.Column('run_id', sa.String(36), nullable=False),
        sa.Column('candidate_id', sa.String(100), nullable=False),
        sa.Column('uploader_id', sa.String(36), nullable=False),
        sa.Column('data', sa.JSON, nullable=False),
        sa.Column('accepted', sa.Boolean, nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('created_at', sa.String(32), nullable=False),
        sa.Column('updated_at', sa.String(32), nullable=False),
        sa.UniqueConstraint('run_id', 'candidate_id', name='uq_pending_run_candidate'))
    op.create_index('ix_pending_transactions_submission_id', 'pending_transactions', ['submission_id'])
    op.create_index('ix_pending_transactions_run_id', 'pending_transactions', ['run_id'])


def downgrade():
    op.drop_table('pending_transactions')
