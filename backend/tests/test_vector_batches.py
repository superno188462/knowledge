"""Contract tests for bounded batches and safe partial failure without real services."""
import json
import unittest
import os
from unittest.mock import AsyncMock, patch

import httpx

from services.rag.gateway_vector_store import GatewayVectorStore, GatewayVectorStoreError


class VectorBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_search_and_delete_resolve_collection_from_environment(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path.endswith('/query'):
                return httpx.Response(200, json={"results": []})
            if request.url.path.endswith('/upsert'):
                return httpx.Response(200, json={"upserted": [{"external_id": "v1"}]})
            return httpx.Response(200, json={"deleted": 1})
        with patch.dict(os.environ, {
            "GATEWAY_VECTOR_BASE_URL": "https://gateway.example/gateway/v1",
            "GATEWAY_VECTOR_COLLECTION": "my collection",
            "GATEWAY_VECTOR_API_KEY": "mock-key",
        }):
            # Each operation starts with a new client, as authenticated API calls do.
            for operation in ('query', 'delete', 'upsert'):
                store = GatewayVectorStore(transport=httpx.MockTransport(handler))
                if operation == 'query':
                    await store.similarity_search('question', metadata={"category": "faq"})
                elif operation == 'delete':
                    await store.delete_vectors(['v1'])
                else:
                    await store.add_vectors([{"id": "v1", "content": "text"}])
                self.assertEqual(requests[-1].url.path, f'/gateway/v1/vector-stores/collections/my collection/{operation}')

    def store(self, handler):
        return GatewayVectorStore("https://gateway.example", "mock-key", "kb", transport=httpx.MockTransport(handler))

    async def test_130_records_use_three_batches_and_preserve_metadata(self):
        sizes = []
        progress = []
        def handler(request):
            self.assertEqual(request.url.path, "/v1/vector-stores/collections/kb/upsert")
            self.assertEqual(request.headers["Authorization"], "Bearer mock-key")
            vectors = json.loads(request.content)["vectors"]
            sizes.append(len(vectors))
            self.assertTrue(all(v["metadata"]["part"] == int(v["id"]) for v in vectors))
            return httpx.Response(200, json={"upserted": [{"external_id": v["id"]} for v in vectors]})
        async def callback(done, total):
            progress.append((done, total))
        result = await self.store(handler).add_vectors([
            {"id": str(i), "content": "chunk", "metadata": {"part": i}} for i in range(130)
        ], on_batch=callback)
        self.assertEqual(sizes, [64, 64, 2])
        self.assertEqual(progress, [(64, 130), (128, 130), (130, 130)])
        self.assertEqual(len(result["upserted"]), 130)

    async def test_partial_failure_reports_confirmed_and_uncertain_ids(self):
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            vectors = json.loads(request.content)["vectors"]
            if calls == 2:
                return httpx.Response(403, json={"detail": {"code": "project_quota_exceeded"}})
            return httpx.Response(200, json={"upserted": [{"external_id": v["id"]} for v in vectors]})
        with self.assertRaises(GatewayVectorStoreError) as caught:
            await self.store(handler).add_vectors([{"id": str(i), "content": "x"} for i in range(65)])
        self.assertEqual(caught.exception.confirmed_ids, [str(i) for i in range(64)])
        self.assertEqual(caught.exception.uncertain_ids, ["64"])
        self.assertEqual(caught.exception.code, "VECTOR_PROJECT_QUOTA_EXCEEDED")
        self.assertEqual(calls, 2)

    async def test_timeout_retries_same_ids_and_preserves_trace(self):
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            raise httpx.ReadTimeout("private", request=request)
        with patch("services.rag.gateway_vector_store.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(GatewayVectorStoreError) as caught:
                await self.store(handler).add_vectors([{"id": "stable", "content": "x"}])
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(body == calls[0] for body in calls))
        self.assertEqual(caught.exception.uncertain_ids, ["stable"])
        self.assertTrue(caught.exception.gateway_trace_id.startswith("knowledge_"))

    async def test_delete_600_ids_in_bounded_batches(self):
        sizes = []
        def handler(request):
            ids = json.loads(request.content)["ids"]
            sizes.append(len(ids))
            return httpx.Response(200, json={"deleted": len(ids)})
        result = await self.store(handler).delete_vectors([str(i) for i in range(600)])
        self.assertEqual(sizes, [256, 256, 88])
        self.assertEqual(result["deleted"], 600)

    async def test_duplicate_across_batches_rejected_before_network(self):
        handler = AsyncMock()
        with self.assertRaises(GatewayVectorStoreError) as caught:
            await self.store(handler).add_vectors([{"id": "same", "content": "x"}] * 65)
        self.assertEqual(caught.exception.code, "VECTOR_DUPLICATE_RECORD_ID")
        handler.assert_not_called()
