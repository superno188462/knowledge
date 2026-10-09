"""业务侧知识文档与切片关系；向量内容由 Gateway 存储。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base


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

