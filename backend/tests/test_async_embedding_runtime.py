"""验证单进程 worker 生命周期，以及切片不占用 API 事件循环。"""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.bootstrap import create_app
from app.modules.knowledge.service import KnowledgeBaseService


class AsyncEmbeddingRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_starts_and_cancels_embedded_worker(self):
        application = create_app()
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def serve():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with (
            patch("app.bootstrap.get_settings", return_value=SimpleNamespace(start_embedding_worker=True)),
            patch("app.bootstrap.embedding_queue.serve", new=serve),
        ):
            async with application.router.lifespan_context(application):
                await asyncio.wait_for(started.wait(), 1)
                task = application.state.embedding_worker_task
                self.assertFalse(task.done())
            self.assertTrue(task.cancelled())
            self.assertTrue(stopped.is_set())
            self.assertIsNone(application.state.embedding_worker_task)

    async def test_disabled_worker_does_not_start(self):
        application = create_app()
        with (
            patch("app.bootstrap.get_settings", return_value=SimpleNamespace(start_embedding_worker=False)),
            patch("app.bootstrap.embedding_queue.serve", new_callable=AsyncMock) as serve,
        ):
            async with application.router.lifespan_context(application):
                self.assertIsNone(application.state.embedding_worker_task)
            serve.assert_not_called()

    async def test_chunking_runs_in_thread_while_event_loop_responds(self):
        started = threading.Event()
        release = threading.Event()
        thread_ids = []
        loop_thread = threading.get_ident()

        def split(*args, **kwargs):
            thread_ids.append(threading.get_ident())
            started.set()
            if not release.wait(2):
                raise RuntimeError("test did not release chunking")
            return [{"content": "片段", "parent_index": None, "parent_content": None}]

        repository = SimpleNamespace(
            get_document_by_path=AsyncMock(return_value=None),
            get_effective_rule=AsyncMock(return_value=None),
            stage_document=AsyncMock(return_value=[]),
            finish_sync=AsyncMock(),
            prepare_embedding_plan=AsyncMock(return_value={"version_id": "v1"}),
            get_record_groups=AsyncMock(return_value={"children": [], "parents": []}),
            set_embedding_plan_state=AsyncMock(), remove_inactive_records=AsyncMock(),
        )
        vectors = SimpleNamespace(
            add_vectors=AsyncMock(return_value={"upserted": ["record"]}),
            publish_version=AsyncMock(),
        )
        service = KnowledgeBaseService(vector_store=vectors, repository=repository)
        service.status = AsyncMock(return_value={"exists": True, "chunk_size": 1000})
        service._target_fingerprint = lambda status: "target"
        with patch("app.modules.knowledge.service.split_document_with_rule", side_effect=split):
            task = asyncio.create_task(service._upsert_documents_unlocked(
                [{"title": "文档", "name": "a.md", "content": "正文"}], owner_user_id="user",
            ))
            try:
                async def wait_for_split():
                    while not started.is_set():
                        await asyncio.sleep(0.001)
                await asyncio.wait_for(wait_for_split(), 1)
                self.assertNotEqual(thread_ids, [loop_thread])
                # 切片线程仍在等待时，事件循环能够完成另一个请求的协程。
                async def api_request():
                    await asyncio.sleep(0)
                    return "ok"
                self.assertEqual(await asyncio.wait_for(api_request(), 0.2), "ok")
                self.assertFalse(task.done())
            finally:
                release.set()
                await task
        vectors.add_vectors.assert_awaited_once()
        repository.finish_sync.assert_awaited_once()
