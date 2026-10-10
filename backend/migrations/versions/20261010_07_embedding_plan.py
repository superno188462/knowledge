"""持久化父子记录发布版本，确保重试与内容回退使用正确版本。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261010_07"
down_revision = "20261009_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_documents", sa.Column("embedding_plan", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_documents", "embedding_plan")
