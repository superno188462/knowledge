"""Store each user's Gateway project API key as authenticated ciphertext.

Revision ID: 20261008_04
Revises: 20261007_03
Create Date: 2026-10-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20261008_04"
down_revision = "20261007_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_user_credentials",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("key_last_four", sa.String(length=4), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("gateway_user_credentials")
