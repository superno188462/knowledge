"""PostgreSQL-backed embedding jobs claimed with row locks and renewable leases."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, case, or_, select, update

from services.auth.database import get_session_factory
from services.gateway_credentials.service import gateway_credential_service
from services.rag.gateway_vector_store import GatewayVectorStoreError
from services.rag.knowledge_base import get_knowledge_base_service
from services.rag.models import KnowledgeDocument, KnowledgeEmbeddingJob
from services.rag.activity_log import add_activity

logger = logging.getLogger(__name__)
_ACTIVE_STATUSES = ("queued", "running")
_LEASE_SECONDS = 180
_HEARTBEAT_SECONDS = 10


def _document_path(document: KnowledgeDocument | None) -> str | None:
    if document is None:
        return None
    return (document.pending_document or {}).get("path") or document.path


def _public_job(job: KnowledgeEmbeddingJob) -> dict[str, object]:
    return {
        "job_id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "stage": job.stage,
        "completed_chunks": job.completed_chunks,
        "total_chunks": job.total_chunks,
        "error_code": job.error_code,
        "attempts": job.attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


class EmbeddingQueue:
    """Enqueue owned documents and run bounded, restartable Gateway embedding work."""

    async def enqueue(self, document_id: str, owner_user_id: str) -> dict[str, object]:
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                document = await session.scalar(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.id == document_id,
                        KnowledgeDocument.owner_user_id == owner_user_id,
                    ).with_for_update()
                )
                if document is None:
                    raise PermissionError("DOCUMENT_NOT_OWNED")
                current = document.pending_document or {}
                if not current.get("content", document.content):
                    raise ValueError("此文档没有已保存的原文，请先上传或保存草稿。")
                existing = await session.scalar(
                    select(KnowledgeEmbeddingJob).where(
                        KnowledgeEmbeddingJob.document_id == document_id,
                        KnowledgeEmbeddingJob.status.in_(_ACTIVE_STATUSES),
                    ).order_by(KnowledgeEmbeddingJob.created_at).limit(1)
                )
                if existing is not None:
                    return _public_job(existing)
                job = KnowledgeEmbeddingJob(
                    id=str(uuid4()), document_id=document_id, owner_user_id=owner_user_id,
                    status="queued", stage="queued", attempts=0, completed_chunks=0,
                )
                session.add(job)
                document.sync_status = "queued"
                document.sync_error_code = None
                add_activity(session, owner_user_id=owner_user_id, document_id=document_id,
                             job_id=job.id, path=_document_path(document),
                             event_type="embedding_queued", status="info", stage="queued")
                await session.flush()
                return _public_job(job)

    async def list_for_user(self, owner_user_id: str, limit: int = 50) -> list[dict[str, object]]:
        factory = get_session_factory()
        async with factory() as session:
            jobs = (await session.scalars(
                select(KnowledgeEmbeddingJob).where(KnowledgeEmbeddingJob.owner_user_id == owner_user_id)
                .order_by(
                    case((KnowledgeEmbeddingJob.status.in_(_ACTIVE_STATUSES), 0), else_=1),
                    KnowledgeEmbeddingJob.created_at.desc(), KnowledgeEmbeddingJob.id.desc(),
                )
                .limit(limit)
            )).all()
            return [_public_job(job) for job in jobs]

    async def get_for_user(self, job_id: str, owner_user_id: str) -> dict[str, object] | None:
        factory = get_session_factory()
        async with factory() as session:
            job = await session.scalar(select(KnowledgeEmbeddingJob).where(
                KnowledgeEmbeddingJob.id == job_id, KnowledgeEmbeddingJob.owner_user_id == owner_user_id,
            ))
            return _public_job(job) if job else None

    async def active_for_documents(self, owner_user_id: str, document_ids: list[str]) -> bool:
        """Reject rule changes that could race with an already running file job."""
        if not document_ids:
            return False
        factory = get_session_factory()
        async with factory() as session:
            return bool(await session.scalar(select(KnowledgeEmbeddingJob.id).where(
                KnowledgeEmbeddingJob.owner_user_id == owner_user_id,
                KnowledgeEmbeddingJob.document_id.in_(set(document_ids)),
                KnowledgeEmbeddingJob.status.in_(_ACTIVE_STATUSES),
            ).limit(1)))

    async def _claim(self) -> tuple[str, str, str, int] | None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=_LEASE_SECONDS)
        factory = get_session_factory()
        async with factory.begin() as session:
            job = await session.scalar(
                select(KnowledgeEmbeddingJob).where(or_(
                    KnowledgeEmbeddingJob.status == "queued",
                    and_(
                        KnowledgeEmbeddingJob.status == "running",
                        or_(
                            KnowledgeEmbeddingJob.heartbeat_at < cutoff,
                            and_(KnowledgeEmbeddingJob.heartbeat_at.is_(None), or_(
                                KnowledgeEmbeddingJob.started_at < cutoff,
                                KnowledgeEmbeddingJob.started_at.is_(None),
                            )),
                        ),
                    ),
                ))
                .order_by(
                    case((KnowledgeEmbeddingJob.status == "queued", 0), else_=1),
                    KnowledgeEmbeddingJob.created_at, KnowledgeEmbeddingJob.id,
                )
                .limit(1).with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            recovered = job.status == "running"
            job.status = "running"
            job.stage = "checking"
            job.attempts += 1
            job.started_at = datetime.now(timezone.utc)
            job.heartbeat_at = job.started_at
            job.completed_chunks = 0
            job.total_chunks = None
            job.error_code = None
            document = await session.get(KnowledgeDocument, job.document_id)
            if document is not None:
                document.sync_status = "syncing"
            if recovered:
                add_activity(session, owner_user_id=job.owner_user_id, document_id=job.document_id,
                             job_id=job.id, path=_document_path(document),
                             event_type="embedding_recovered", status="info", stage="checking")
            add_activity(session, owner_user_id=job.owner_user_id, document_id=job.document_id,
                         job_id=job.id, path=_document_path(document),
                         event_type="embedding_started", status="info", stage="checking")
            return job.id, job.document_id, job.owner_user_id, job.attempts

    async def _heartbeat(self, job_id: str, attempt: int) -> None:
        factory = get_session_factory()
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            try:
                async with factory.begin() as session:
                    result = await session.execute(update(KnowledgeEmbeddingJob).where(
                        KnowledgeEmbeddingJob.id == job_id,
                        KnowledgeEmbeddingJob.status == "running",
                        KnowledgeEmbeddingJob.attempts == attempt,
                    ).values(heartbeat_at=datetime.now(timezone.utc)))
                    if result.rowcount == 0:
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("embedding_job_heartbeat_failed job_id=%s", job_id)

    async def _progress(self, job_id: str, attempt: int, stage: str, completed: int, total: int | None) -> None:
        factory = get_session_factory()
        async with factory.begin() as session:
            job = await session.get(KnowledgeEmbeddingJob, job_id, with_for_update=True)
            if job is None or job.status != "running" or job.attempts != attempt:
                return
            previous_completed = job.completed_chunks
            changed = job.stage != stage or (stage == "embedding" and completed > previous_completed)
            job.stage = stage
            job.completed_chunks = completed
            job.total_chunks = total
            job.heartbeat_at = datetime.now(timezone.utc)
            if changed:
                document = await session.get(KnowledgeDocument, job.document_id)
                if stage == "embedding" and completed > previous_completed:
                    for index in range(previous_completed, completed):
                        add_activity(session, owner_user_id=job.owner_user_id, document_id=job.document_id,
                                     job_id=job.id, path=_document_path(document),
                                     event_type="embedding_chunk_completed", status="info", stage=stage,
                                     chunk_index=index, completed_chunks=index + 1, total_chunks=total)
                else:
                    add_activity(session, owner_user_id=job.owner_user_id, document_id=job.document_id,
                                 job_id=job.id, path=_document_path(document),
                                 event_type=f"embedding_{stage}", status="info", stage=stage,
                                 completed_chunks=completed, total_chunks=total)

    async def _finish(
        self, job_id: str, document_id: str, attempt: int, error_code: str | None,
        gateway_status: int | None = None, gateway_trace_id: str | None = None,
        upstream_code: str | None = None,
    ) -> None:
        factory = get_session_factory()
        async with factory.begin() as session:
            job = await session.get(KnowledgeEmbeddingJob, job_id, with_for_update=True)
            if job is None or job.status != "running" or job.attempts != attempt:
                return
            job.status = "failed" if error_code else "succeeded"
            job.stage = job.status
            job.error_code = error_code
            job.finished_at = datetime.now(timezone.utc)
            job.heartbeat_at = None
            document = await session.get(KnowledgeDocument, document_id)
            add_activity(session, owner_user_id=job.owner_user_id, document_id=document_id,
                         job_id=job.id, path=_document_path(document),
                         event_type="embedding_failed" if error_code else "embedding_succeeded",
                         status="error" if error_code else "success", stage=job.stage,
                         completed_chunks=job.completed_chunks, total_chunks=job.total_chunks,
                         error_code=error_code, gateway_status=gateway_status,
                         gateway_trace_id=gateway_trace_id, upstream_code=upstream_code)
            if error_code:
                if document is not None:
                    document.sync_status = "failed"
                    document.sync_error_code = error_code

    async def _process(self, claimed: tuple[str, str, str, int]) -> None:
        job_id, document_id, owner_user_id, attempt = claimed
        error_code: str | None = None
        gateway_status: int | None = None
        gateway_trace_id: str | None = None
        upstream_code: str | None = None
        heartbeat_task = asyncio.create_task(self._heartbeat(job_id, attempt))
        try:
            api_key = await gateway_credential_service.get_key(owner_user_id)
            service = get_knowledge_base_service(api_key=api_key)

            async def progress(stage: str, completed: int, total: int | None) -> None:
                await self._progress(job_id, attempt, stage, completed, total)

            await service.reindex_document(
                document_id, owner_user_id=owner_user_id, progress=progress
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_code = getattr(exc, "code", None) or "EMBEDDING_JOB_FAILED"
            if not isinstance(error_code, str) or len(error_code) > 80:
                error_code = "EMBEDDING_JOB_FAILED"
            if isinstance(exc, GatewayVectorStoreError):
                gateway_status = exc.status_code
                gateway_trace_id = (exc.gateway_trace_id or "")[:100] or None
                upstream_code = (exc.upstream_code or "")[:80] or None
            logger.warning(
                "embedding_job_failed job_id=%s document_id=%s code=%s gateway_status=%s trace_id=%s",
                job_id, document_id, error_code, gateway_status, gateway_trace_id,
            )
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        await self._finish(job_id, document_id, attempt, error_code,
                           gateway_status, gateway_trace_id, upstream_code)

    async def _worker(self) -> None:
        while True:
            claimed = await self._claim()
            if claimed is None:
                await asyncio.sleep(1)
                continue
            await self._process(claimed)

    async def serve(self) -> None:
        """Multiple processes may safely claim jobs; stale leases are reclaimed."""
        workers_count = max(1, min(16, int(os.getenv("KNOWLEDGE_EMBEDDING_WORKERS", "3"))))
        while True:
            try:
                logger.info("embedding_queue_started workers=%s lease_seconds=%s", workers_count, _LEASE_SECONDS)
                async with asyncio.TaskGroup() as group:
                    for _ in range(workers_count):
                        group.create_task(self._worker())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("embedding_queue_worker_error")
                await asyncio.sleep(3)


embedding_queue = EmbeddingQueue()
