"""按用户选择集合；嵌入任务记录提交时的目标，不迁移或删除原数据。"""
from alembic import op
import sqlalchemy as sa

revision = "20261009_06"
down_revision = "20261009_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gateway_user_credentials", sa.Column("selected_collection", sa.String(200), nullable=True))
    op.add_column("gateway_user_credentials", sa.Column("selected_collection_id", sa.String(200), nullable=True))
    op.add_column("knowledge_embedding_jobs", sa.Column("target_collection", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_embedding_jobs", "target_collection")
    op.drop_column("gateway_user_credentials", "selected_collection_id")
    op.drop_column("gateway_user_credentials", "selected_collection")
