"""Persistent, user-scoped knowledge activity history without source text or credentials."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_session_factory
from app.modules.activity_logs.models import KnowledgeActivityLog
from .repository import list_activity


def add_activity(
    session: AsyncSession, *, owner_user_id: str, event_type: str, status: str = "info",
    document_id: str | None = None, job_id: str | None = None, path: str | None = None,
    stage: str | None = None, chunk_index: int | None = None, completed_chunks: int | None = None,
    total_chunks: int | None = None, error_code: str | None = None,
    gateway_status: int | None = None, gateway_trace_id: str | None = None,
    upstream_code: str | None = None,
) -> None:
    session.add(KnowledgeActivityLog(
        id=str(uuid4()), owner_user_id=owner_user_id, event_type=event_type,
        status=status, document_id=document_id, job_id=job_id, path=path,
        stage=stage, chunk_index=chunk_index, completed_chunks=completed_chunks, total_chunks=total_chunks,
        error_code=error_code, gateway_status=gateway_status,
        gateway_trace_id=gateway_trace_id, upstream_code=upstream_code,
    ))


async def record_activity(**fields: object) -> None:
    factory = get_session_factory()
    async with factory.begin() as session:
        add_activity(session, **fields)


