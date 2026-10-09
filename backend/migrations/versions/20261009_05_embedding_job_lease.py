"""Lease individual embedding jobs instead of relying on a session advisory leader lock.

Revision ID: 20261009_05
Revises: 20261009_04
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_05"
down_revision = "20261009_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_embedding_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_embedding_jobs", "heartbeat_at")
