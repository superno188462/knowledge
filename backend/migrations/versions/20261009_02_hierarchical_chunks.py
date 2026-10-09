"""Store local parent context for opt-in hierarchical chunking.

Revision ID: 20261009_02
Revises: 20261009_01
"""

from alembic import op
import sqlalchemy as sa


revision = "20261009_02"
down_revision = "20261009_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_chunks", sa.Column("parent_id", sa.String(length=200), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("parent_content", sa.Text(), nullable=True))
    op.create_index("ix_knowledge_chunks_parent_id", "knowledge_chunks", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_parent_id", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "parent_content")
    op.drop_column("knowledge_chunks", "parent_id")
