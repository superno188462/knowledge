"""Store safe Gateway diagnostics alongside failed embedding events.

Revision ID: 20261009_04
Revises: 20261009_03
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_04"
down_revision = "20261009_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_activity_logs", sa.Column("gateway_status", sa.Integer(), nullable=True))
    op.add_column("knowledge_activity_logs", sa.Column("gateway_trace_id", sa.String(length=100), nullable=True))
    op.add_column("knowledge_activity_logs", sa.Column("upstream_code", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_activity_logs", "upstream_code")
    op.drop_column("knowledge_activity_logs", "gateway_trace_id")
    op.drop_column("knowledge_activity_logs", "gateway_status")
