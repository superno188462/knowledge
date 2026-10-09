"""Persist embedding jobs for ordered background processing.

Revision ID: 20261008_05
Revises: 20261009_02
"""

from alembic import op
import sqlalchemy as sa


revision = "20261008_05"
down_revision = "20261009_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_embedding_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("document_id", sa.String(length=200), sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("completed_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_knowledge_embedding_jobs_status_created", "knowledge_embedding_jobs", ["status", "created_at"])
    op.create_index("ix_knowledge_embedding_jobs_document_id", "knowledge_embedding_jobs", ["document_id"])
    op.create_index("ix_knowledge_embedding_jobs_owner_user_id", "knowledge_embedding_jobs", ["owner_user_id"])
    op.create_index(
        "uq_knowledge_embedding_jobs_active_document",
        "knowledge_embedding_jobs",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_knowledge_embedding_jobs_active_document", table_name="knowledge_embedding_jobs")
    op.drop_index("ix_knowledge_embedding_jobs_owner_user_id", table_name="knowledge_embedding_jobs")
    op.drop_index("ix_knowledge_embedding_jobs_document_id", table_name="knowledge_embedding_jobs")
    op.drop_index("ix_knowledge_embedding_jobs_status_created", table_name="knowledge_embedding_jobs")
    op.drop_table("knowledge_embedding_jobs")
