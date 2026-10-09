"""Track the Gateway collection that confirmed each document generation.

Revision ID: 20261008_06
Revises: 20261008_05
"""

from alembic import op
import sqlalchemy as sa


revision = "20261008_06"
down_revision = "20261008_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("embedded_target_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("target_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_chunks", "target_fingerprint")
    op.drop_column("knowledge_documents", "embedded_target_fingerprint")
