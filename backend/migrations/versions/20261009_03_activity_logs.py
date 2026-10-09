"""Add persistent knowledge activity logs.

Revision ID: 20261009_03
Revises: 20261008_06
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_03"
down_revision = "20261008_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_activity_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=200), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("path", sa.String(length=1000), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("completed_chunks", sa.Integer(), nullable=True),
        sa.Column("total_chunks", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_knowledge_activity_logs_owner_time", "knowledge_activity_logs", ["owner_user_id", "created_at", "id"])
    op.create_index("ix_knowledge_activity_logs_owner_document", "knowledge_activity_logs", ["owner_user_id", "document_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_activity_logs_owner_document", table_name="knowledge_activity_logs")
    op.drop_index("ix_knowledge_activity_logs_owner_time", table_name="knowledge_activity_logs")
    op.drop_table("knowledge_activity_logs")
