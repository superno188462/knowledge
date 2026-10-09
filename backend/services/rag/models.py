"""业务侧知识文档与切片关系；向量内容由 Gateway 存储。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.auth.database import Base


class KnowledgeDocument(Base):
    """保存可重新解析/切片的原文及其业务 metadata。"""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("uq_knowledge_documents_owner_path", "owner_user_id", "path", unique=True),
    )

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Nullable only for pre-identity documents retained for explicit owner reassignment.
    owner_user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    file_name: Mapped[str | None] = mapped_column(String(500))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedded_target_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunking_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pending_document: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    sync_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class KnowledgeChunk(Base):
    """保存业务侧切片内容、稳定向量 ID、序号及独立 metadata。"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (Index("ix_knowledge_chunks_document_index", "document_id", "chunk_index"),)

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    parent_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    target_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


class KnowledgeDirectory(Base):
    """用户可管理的目录节点及该层可选切片规则覆盖。"""

    __tablename__ = "knowledge_directories"
    __table_args__ = (UniqueConstraint("owner_user_id", "path", name="uq_knowledge_directories_owner_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    chunking_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class KnowledgeEmbeddingJob(Base):
    """Durable per-document embedding request, claimed by one database-backed worker."""

    __tablename__ = "knowledge_embedding_jobs"
    __table_args__ = (
        Index("ix_knowledge_embedding_jobs_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    completed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeActivityLog(Base):
    """User-scoped, content-free audit trail that survives document deletion."""

    __tablename__ = "knowledge_activity_logs"
    __table_args__ = (
        Index("ix_knowledge_activity_logs_owner_time", "owner_user_id", "created_at", "id"),
        Index("ix_knowledge_activity_logs_owner_document", "owner_user_id", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(24), nullable=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    gateway_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gateway_trace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    upstream_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
