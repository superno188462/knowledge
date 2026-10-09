"""Add managed directories and per-scope chunking overrides.

Revision ID: 20261009_01
Revises: 20261008_04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261009_01"
down_revision = "20261008_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("chunking_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("pending_document", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("knowledge_documents", sa.Column("sync_error_code", sa.String(length=80), nullable=True))
    # Keep old active records while a new chunk generation is written. The unique
    # (document_id, chunk_index) constraint prevented a failure-safe replacement.
    op.drop_index("uq_knowledge_chunks_document_index", table_name="knowledge_chunks")
    op.create_index("ix_knowledge_chunks_document_index", "knowledge_chunks", ["document_id", "chunk_index"])
    op.create_table(
        "knowledge_directories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("chunking_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "path", name="uq_knowledge_directories_owner_path"),
    )
    op.create_index("ix_knowledge_directories_owner_user_id", "knowledge_directories", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_directories_owner_user_id", table_name="knowledge_directories")
    op.drop_table("knowledge_directories")
    op.execute("DELETE FROM knowledge_chunks WHERE is_active = false")
    op.drop_index("ix_knowledge_chunks_document_index", table_name="knowledge_chunks")
    op.create_index(
        "uq_knowledge_chunks_document_index", "knowledge_chunks", ["document_id", "chunk_index"], unique=True
    )
    op.drop_column("knowledge_documents", "pending_document")
    op.drop_column("knowledge_documents", "sync_error_code")
    op.drop_column("knowledge_documents", "chunking_config")
