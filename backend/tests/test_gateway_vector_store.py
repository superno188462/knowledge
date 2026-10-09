"""Gateway 向量 API 与本地知识库用例合同测试，网络由 MockTransport 截获。"""

from __future__ import annotations

import json
import unittest

import httpx

from services.rag.gateway_vector_store import GatewayVectorStore, GatewayVectorStoreError
from services.rag.knowledge_base import KnowledgeBaseService


class MemoryRepository:
    """让业务流程测试不连接 PostgreSQL。"""

    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.chunks: dict[str, dict[str, dict]] = {}

    async def get_document_by_path(self, path, owner_user_id):
        document = next((item for item in self.documents.values() if item.get("path") == path and item.get("owner_user_id") == owner_user_id), None)
        if document is None:
            return None
        return {
            "document_id": document["document_id"],
            "content_sha256": document["content_sha256"],
            "title": document["title"],
            "metadata": document["metadata"],
            "embedded_target_fingerprint": document.get("embedded_target_fingerprint"),
            "sync_status": document.get("sync_status"),
        }

    async def stage_document(self, **values):
        document_id = values["document_id"]
        old_ids = [
            chunk_id for chunk_id, chunk in self.chunks.get(document_id, {}).items()
            if chunk.get("target_fingerprint") == values["target_fingerprint"]
        ]
        self.documents[document_id] = {**values, "document_id": document_id, "sync_status": "syncing"}
        current = self.chunks.setdefault(document_id, {})
        current.update({chunk["id"]: {**chunk, "target_fingerprint": values["target_fingerprint"]} for chunk in values["chunks"]})
        return old_ids

    async def finish_sync(self, document_id, active_ids, target_fingerprint):
        self.chunks[document_id] = {
            chunk_id: chunk for chunk_id, chunk in self.chunks[document_id].items()
            if chunk_id in set(active_ids)
        }
        self.documents[document_id]["sync_status"] = "ready"
        self.documents[document_id]["embedded_target_fingerprint"] = target_fingerprint

    async def mark_failed(self, document_id, error_code=None):
        self.documents[document_id]["sync_status"] = "failed"
        self.documents[document_id]["sync_error_code"] = error_code

    async def get_effective_rule(self, path, owner_user_id, document_rule=None):
        return document_rule

    async def get_parent_contexts(self, owner_user_id, parent_ids):
        return {}

    async def get_chunk_ids(self, document_id, owner_user_id):
        if self.documents.get(document_id, {}).get("owner_user_id") != owner_user_id:
            return []
        return list(self.chunks.get(document_id, {}))

    async def delete_local_document(self, document_id, owner_user_id):
        if self.documents.get(document_id, {}).get("owner_user_id") != owner_user_id:
            return
        self.documents.pop(document_id, None)
        self.chunks.pop(document_id, None)

    async def list_documents(self, owner_user_id):
        return [
            {"document_id": key, "title": value["title"], "sync_status": value["sync_status"],
             "chunk_count": len(self.chunks.get(key, {}))}
            for key, value in self.documents.items() if value.get("owner_user_id") == owner_user_id
        ]

    async def document_exists(self, document_id):
        return document_id in self.documents

    async def get_document_by_id(self, document_id, owner_user_id):
        document = self.documents.get(document_id)
        return document if document and document.get("owner_user_id") == owner_user_id else None


class GatewayVectorStoreTests(unittest.IsolatedAsyncioTestCase):
    def make_store(self, handler) -> GatewayVectorStore:
        return GatewayVectorStore(
            "https://gateway.example/gateway/v1/",
            "project-key-for-test",
            "knowledge-kb-test",
            transport=httpx.MockTransport(handler),
        )

    async def test_environment_requires_only_url_key_and_collection(self) -> None:
        import os

        names = {
            "GATEWAY_BASE_URL": "https://gateway.example/v1",
            "GATEWAY_API_KEY": "test-only-key",
            "GATEWAY_COLLECTION": "knowledge-kb-test",
        }
        previous = {key: os.environ.get(key) for key in names}
        try:
            for key in names:
                os.environ.pop(key, None)
            store = GatewayVectorStore(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[])))
            for key, value in names.items():
                os.environ[key] = value
            self.assertEqual(await store.list_collections(), [])
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    async def test_collection_creation_fixes_model_and_record_limit(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=[])
            return httpx.Response(201, json={
                "collection": "knowledge-kb-test", "model": "example/embed-small",
                "vector_dimensions": 768, "chunk_size": 700, "chunk_overlap": 0,
            })

        item = await self.make_store(handler).ensure_collection(
            model="example/embed-small", description="test", chunk_size=700
        )
        self.assertEqual([request.method for request in requests], ["GET", "POST"])
        self.assertEqual(json.loads(requests[1].content), {
            "name": "knowledge-kb-test", "description": "test", "model": "example/embed-small",
            "chunk_size": 700, "chunk_overlap": 0,
        })
        self.assertEqual(item["vector_dimensions"], 768)

        mismatch = self.make_store(lambda _request: httpx.Response(200, json=[{
            "collection": "knowledge-kb-test", "model": "different", "chunk_size": 700,
        }]))
        with self.assertRaises(GatewayVectorStoreError) as caught:
            await mismatch.ensure_collection(model="example/embed-small", description="test", chunk_size=700)
        self.assertEqual(caught.exception.code, "VECTOR_COLLECTION_MODEL_MISMATCH")

    async def test_add_vectors_sends_individual_records_and_metadata(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"upserted": [{"external_id": "doc-0", "vectors": 1}]})

        store = self.make_store(handler)
        vectors = [{"id": "doc-0", "content": "pre-split text", "metadata": {"document_id": "doc"}}]
        await store.add_vectors(vectors)
        self.assertEqual(captured[0].url.path, "/gateway/v1/vector-stores/collections/knowledge-kb-test/upsert")
        self.assertEqual(json.loads(captured[0].content), {"vectors": vectors})

    async def test_document_upsert_splits_persists_and_removes_stale_vectors(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=[{
                    "collection": "knowledge-kb-test", "model": "example/embed-small", "chunk_size": 100,
                    "vector_dimensions": 768,
                }])
            if request.url.path.endswith("/upsert"):
                vectors = json.loads(request.content)["vectors"]
                return httpx.Response(200, json={"upserted": [{"external_id": item["id"]} for item in vectors]})
            if request.url.path.endswith("/delete"):
                return httpx.Response(200, json={"deleted": len(json.loads(request.content)["ids"])})
            return httpx.Response(200, json={"results": []})

        repository = MemoryRepository()
        service = KnowledgeBaseService(self.make_store(handler), repository=repository)
        first = await service.upsert_document(
            owner_user_id="owner-a",
            document_id="manual-1", title="说明", file_name="guide.md",
            content="连接配置说明。" * 180, metadata={"category": "technical"},
        )
        first_batches = [
            json.loads(request.content)["vectors"]
            for request in requests if request.url.path.endswith("/upsert")
        ]
        first_vectors = [item for batch in first_batches for item in batch]
        self.assertGreater(len(first_vectors), 1)
        self.assertEqual(len(first_batches), 1)
        self.assertTrue(all(1 <= len(batch) <= 64 for batch in first_batches))
        self.assertTrue(all(len(item["content"]) <= 100 for item in first_vectors))
        self.assertEqual(first_vectors[0]["metadata"]["document_id"], "manual-1")
        self.assertEqual(first_vectors[0]["metadata"]["chunk_index"], 0)
        self.assertEqual(first_vectors[0]["metadata"]["file_name"], "guide.md")
        self.assertEqual(first["result"]["chunks"], len(first_vectors))

        old_ids = [item["id"] for item in first_vectors]
        second = await service.upsert_document(
            owner_user_id="owner-a",
            document_id="manual-1", title="说明", file_name="guide.md",
            content="更新后文本。", metadata={"category": "technical"},
        )
        self.assertEqual(second["id"], "manual-1")
        self.assertEqual(len(repository.chunks["manual-1"]), 1)
        upsert_requests = [request for request in requests if request.url.path.endswith("/upsert")]
        second_vectors = json.loads(upsert_requests[-1].content)["vectors"]
        self.assertNotEqual(second_vectors[0]["id"], first_vectors[0]["id"])
        deleted_requests = [request for request in requests if request.url.path.endswith("/delete")]
        self.assertEqual(set(json.loads(deleted_requests[-1].content)["ids"]), set(old_ids))

    async def test_partial_gateway_confirmation_keeps_document_failed(self) -> None:
        requests: list[httpx.Request] = []
        upserts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal upserts
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=[{"collection": "knowledge-kb-test", "chunk_size": 100}])
            if request.url.path.endswith("/upsert"):
                upserts += 1
                vectors = json.loads(request.content)["vectors"]
                return httpx.Response(200, json={
                    "upserted": [{"external_id": vectors[0]["id"]}] if upserts == 1 else []
                })
            return httpx.Response(200, json={"deleted": 0})

        repository = MemoryRepository()
        service = KnowledgeBaseService(self.make_store(handler), repository=repository)
        with self.assertRaises(GatewayVectorStoreError) as caught:
            await service.upsert_document(
                owner_user_id="owner-a", document_id="partial-1", title="partial.txt",
                content="A" * 190, metadata={},
            )
        self.assertEqual(caught.exception.code, "VECTOR_GATEWAY_INCOMPLETE_UPSERT")
        self.assertEqual(repository.documents["partial-1"]["sync_status"], "failed")
        self.assertTrue(any(request.url.path.endswith("/delete") for request in requests))

    async def test_metadata_filter_results_group_by_parent_document(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"results": [
                {"chunk_id": "c1", "external_id": "v1", "title": "Doc", "content": "second",
                 "score": .9, "metadata": {"document_id": "d1", "chunk_index": 1, "category": "guide"}},
                {"chunk_id": "c0", "external_id": "v0", "title": "Doc", "content": "first",
                 "score": .8, "metadata": {"document_id": "d1", "chunk_index": 0, "category": "guide"}},
            ]})

        service = KnowledgeBaseService(self.make_store(handler), repository=MemoryRepository())
        hits = await service.search("query", owner_user_id="owner-a", top_k=2, metadata={"category": "guide"})
        self.assertEqual(hits[0]["document_id"], "d1")
        self.assertEqual(json.loads(requests[0].content)["metadata"], {
            "category": "guide", "app_id": "knowledge-platform",
            "record_type": "knowledge_chunk", "owner_user_id": "owner-a",
        })
        ordered_chunks = sorted(hits, key=lambda hit: hit["metadata"]["chunk_index"])
        self.assertEqual([hit["content"] for hit in ordered_chunks], ["first", "second"])

    async def test_search_excludes_new_chunks_until_document_is_committed(self) -> None:
        class ActiveRepository(MemoryRepository):
            async def get_active_chunk_signatures(self, owner_user_id, document_ids, target_fingerprint):
                return {"old-vector-id"}, {("doc-1", 0, "old-hash")}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json=[{"collection": "knowledge-kb-test", "id": "test-collection", "chunk_size": 1000}])
            return httpx.Response(200, json={"results": [
                {"chunk_id": "gateway-new", "content": "unconfirmed", "score": .99,
                 "metadata": {"document_id": "doc-1", "chunk_index": 0,
                              "chunk_sha256": "new-hash", "vector_record_id": "new-vector-id"}},
                {"chunk_id": "gateway-old", "content": "confirmed", "score": .8,
                 "metadata": {"document_id": "doc-1", "chunk_index": 0,
                              "chunk_sha256": "old-hash", "vector_record_id": "old-vector-id"}},
            ]})

        service = KnowledgeBaseService(self.make_store(handler), repository=ActiveRepository())
        hits = await service.search("question", owner_user_id="owner-a", top_k=2)
        self.assertEqual([item["content"] for item in hits], ["confirmed"])

    async def test_search_accepts_legacy_active_chunk_without_vector_id(self) -> None:
        import hashlib

        class ActiveRepository(MemoryRepository):
            async def get_active_chunk_signatures(self, owner_user_id, document_ids, target_fingerprint):
                digest = hashlib.sha256("legacy content".encode("utf-8")).hexdigest()
                return set(), {("doc-1", 0, digest)}

        def handler(_request: httpx.Request) -> httpx.Response:
            if _request.method == "GET":
                return httpx.Response(200, json=[{"collection": "knowledge-kb-test", "id": "test-collection", "chunk_size": 1000}])
            return httpx.Response(200, json={"results": [{
                "chunk_id": "legacy-vector", "content": "legacy content", "score": .8,
                "metadata": {"document_id": "doc-1", "chunk_index": 0},
            }]})

        service = KnowledgeBaseService(self.make_store(handler), repository=ActiveRepository())
        hits = await service.search("question", owner_user_id="owner-a", top_k=1)
        self.assertEqual([item["content"] for item in hits], ["legacy content"])

    async def test_delete_removes_every_record_before_local_document(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"results": []} if request.url.path.endswith("/query") else {"deleted": 2})

        repository = MemoryRepository()
        repository.documents["doc"] = {"title": "doc", "sync_status": "ready", "owner_user_id": "owner-a"}
        repository.chunks["doc"] = {"v1": {}, "v2": {}}
        service = KnowledgeBaseService(self.make_store(handler), repository=repository)
        await service.delete_document("doc", owner_user_id="owner-a")
        self.assertEqual(json.loads(requests[0].content), {"ids": ["v1", "v2"]})
        self.assertNotIn("doc", repository.documents)

    async def test_gateway_error_does_not_expose_upstream_detail_or_key(self) -> None:
        store = self.make_store(lambda _request: httpx.Response(401, json={"detail": "private secret"}))
        with self.assertRaises(GatewayVectorStoreError) as caught:
            await store.list_collections()
        self.assertEqual(caught.exception.code, "VECTOR_GATEWAY_AUTH_FAILED")
        self.assertNotIn("private secret", str(caught.exception))
        self.assertNotIn("project-key-for-test", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
