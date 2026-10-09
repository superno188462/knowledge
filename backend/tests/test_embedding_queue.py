"""Verify concurrent file processing and safe queue failure states without a real Gateway."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.embedding_jobs.worker import EmbeddingQueue
from app.integrations.gateway import GatewayVectorStoreError


class MemoryQueue(EmbeddingQueue):
    def __init__(self) -> None:
        self.progress: list[tuple[str, str, int, int | None]] = []
        self.finished: list[tuple[str, str, str | None]] = []

    async def _heartbeat(self, job_id, attempt):
        await asyncio.Event().wait()

    async def _progress(self, job_id: str, attempt: int, stage: str, completed: int, total: int | None) -> None:
        self.progress.append((job_id, stage, completed, total))

    async def _finish(self, job_id: str, document_id: str, attempt: int, error_code: str | None, *diagnostics) -> None:
        self.finished.append((job_id, document_id, error_code))


class EmbeddingQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_uses_row_lock_and_sets_lease_for_recovered_job(self):
        from contextlib import asynccontextmanager
        from sqlalchemy.dialects import postgresql
        job = SimpleNamespace(id="job", document_id="doc", owner_user_id="owner",
                              status="running", attempts=1)
        queries = []
        class Session:
            async def scalar(self, query):
                queries.append(str(query.compile(dialect=postgresql.dialect())))
                return job
            async def get(self, model, identifier):
                return None
        class Factory:
            @asynccontextmanager
            async def begin(self):
                yield Session()
        with (
            patch("app.modules.embedding_jobs.worker.get_session_factory", return_value=Factory()),
            patch("app.modules.embedding_jobs.worker.add_activity") as activity,
        ):
            claimed = await EmbeddingQueue()._claim()
        self.assertEqual(claimed, ("job", "doc", "owner", 2, None))
        self.assertIn("FOR UPDATE SKIP LOCKED", queries[0])
        self.assertIn("heartbeat_at", queries[0])
        self.assertIsNotNone(job.heartbeat_at)
        self.assertEqual(activity.call_count, 2)

    async def test_old_attempt_cannot_finish_reclaimed_job(self):
        from contextlib import asynccontextmanager
        job = SimpleNamespace(status="running", attempts=2)
        class Session:
            async def get(self, *args, **kwargs):
                return job
        class Factory:
            @asynccontextmanager
            async def begin(self):
                yield Session()
        with (
            patch("app.modules.embedding_jobs.worker.get_session_factory", return_value=Factory()),
            patch("app.modules.embedding_jobs.worker.add_activity") as activity,
        ):
            await EmbeddingQueue()._finish("job", "doc", 1, "OLD_ERROR")
        self.assertEqual(job.status, "running")
        activity.assert_not_called()

    async def test_two_documents_can_process_at_the_same_time(self) -> None:
        queue = MemoryQueue()
        both_started = asyncio.Event()
        active = 0
        peak = 0

        class FakeService:
            async def reindex_document(self, document_id, *, owner_user_id, progress):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                if active == 2:
                    both_started.set()
                await progress("chunking", 0, 2)
                await asyncio.wait_for(both_started.wait(), timeout=1)
                await progress("embedding", 2, 2)
                active -= 1

        with (
            patch("app.modules.embedding_jobs.worker.gateway_credential_service.get_key", new=AsyncMock(return_value="test-key")),
            patch("app.modules.embedding_jobs.worker.get_knowledge_base_service", return_value=FakeService()),
        ):
            await asyncio.gather(
                queue._process(("job-1", "doc-1", "owner-1", 1, "docs")),
                queue._process(("job-2", "doc-2", "owner-1", 1, "docs")),
            )

        self.assertEqual(peak, 2)
        self.assertEqual({item[0] for item in queue.finished}, {"job-1", "job-2"})
        self.assertTrue(all(item[2] is None for item in queue.finished))
        self.assertEqual(sum(stage == "embedding" for _, stage, _, _ in queue.progress), 2)

    async def test_timeout_marks_job_failed_without_claiming_success(self) -> None:
        queue = MemoryQueue()

        class FakeService:
            async def reindex_document(self, document_id, *, owner_user_id, progress):
                await progress("embedding", 1, 3)
                raise GatewayVectorStoreError("VECTOR_GATEWAY_TIMEOUT")

        with (
            patch("app.modules.embedding_jobs.worker.gateway_credential_service.get_key", new=AsyncMock(return_value="test-key")),
            patch("app.modules.embedding_jobs.worker.get_knowledge_base_service", return_value=FakeService()),
        ):
            await queue._process(("job-3", "doc-3", "owner-1", 1, "docs"))

        self.assertEqual(queue.finished, [("job-3", "doc-3", "VECTOR_GATEWAY_TIMEOUT")])
        self.assertEqual(queue.progress, [("job-3", "embedding", 1, 3)])
