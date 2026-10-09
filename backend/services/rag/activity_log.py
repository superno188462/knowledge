"""Persistent, user-scoped knowledge activity history without source text or credentials."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.auth.database import get_session_factory
from services.rag.models import KnowledgeActivityLog


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


async def list_activity(
    owner_user_id: str, *, page: int, page_size: int,
    document_id: str | None = None, event_type: str | None = None,
    path_query: str | None = None,
) -> dict[str, object]:
    conditions = [KnowledgeActivityLog.owner_user_id == owner_user_id]
    if document_id:
        conditions.append(KnowledgeActivityLog.document_id == document_id)
    if event_type:
        conditions.append(KnowledgeActivityLog.event_type == event_type)
    if path_query:
        escaped = path_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(KnowledgeActivityLog.path.ilike(f"%{escaped}%", escape="\\"))
    factory = get_session_factory()
    async with factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(KnowledgeActivityLog).where(*conditions)
        ) or 0
        rows = (await session.scalars(
            select(KnowledgeActivityLog).where(*conditions)
            .order_by(KnowledgeActivityLog.created_at.desc(), KnowledgeActivityLog.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )).all()
    return {
        "total": total, "page": page, "page_size": page_size,
        "logs": [{
            "id": row.id, "event_type": row.event_type, "status": row.status,
            "document_id": row.document_id, "job_id": row.job_id, "path": row.path,
            "stage": row.stage, "chunk_index": row.chunk_index,
            "completed_chunks": row.completed_chunks,
            "total_chunks": row.total_chunks, "error_code": row.error_code,
            "gateway_status": row.gateway_status, "gateway_trace_id": row.gateway_trace_id,
            "upstream_code": row.upstream_code,
            "created_at": row.created_at.isoformat(),
        } for row in rows],
    }
