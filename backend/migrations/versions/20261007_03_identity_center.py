"""Replace local accounts with Identity Center and isolate document ownership.

Revision ID: 20261007_03
Revises: 20261005_05
Create Date: 2026-10-07

Existing knowledge documents/chunks and Gateway vectors are retained. Their new nullable
owner_user_id stays NULL until an operator explicitly reassigns legacy records. Local
password hashes and sessions are removed because this application no longer authenticates
them; accounts must be created or used in Identity Center after this migration.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261007_03"
down_revision = "20261005_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_knowledge_documents_owner_user_id",
        "knowledge_documents",
        ["owner_user_id"],
    )
    op.drop_index("uq_knowledge_documents_path", table_name="knowledge_documents")
    op.create_index(
        "uq_knowledge_documents_owner_path",
        "knowledge_documents",
        ["owner_user_id", "path"],
        unique=True,
    )

    # These tables contain only retired local-auth state; no business document rows are
    # deleted. Dropping them removes locally held password hashes and local sessions.
    op.drop_table("app_login_attempts")
    op.drop_table("app_auth_sessions")
    op.drop_table("app_users")


def downgrade() -> None:
    # Schema rollback is possible, but retired credentials/accounts are intentionally not
    # reconstructed. Users must authenticate through Identity Center after rollback too.
    op.create_table(
        "app_users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("role", sa.String(length=16), server_default="user", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_app_users_username"),
    )
    op.create_table(
        "app_auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_app_auth_sessions_token_hash"),
    )
    op.create_index("ix_app_auth_sessions_user_id", "app_auth_sessions", ["user_id"])
    op.create_index("ix_app_auth_sessions_expires_at", "app_auth_sessions", ["expires_at"])
    op.create_table(
        "app_login_attempts",
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("scope", "key_hash", name="pk_app_login_attempts"),
    )
    op.create_index("ix_app_login_attempts_updated_at", "app_login_attempts", ["updated_at"])

    op.drop_index("uq_knowledge_documents_owner_path", table_name="knowledge_documents")
    op.create_index("uq_knowledge_documents_path", "knowledge_documents", ["path"], unique=True)
    op.drop_index("ix_knowledge_documents_owner_user_id", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "owner_user_id")
