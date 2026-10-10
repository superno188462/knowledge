"""手工真实网关联调：uv run python tests/live_gateway_parent_child.py。
创建唯一临时集合，finally 删除；不修改 Knowledge 业务数据库，不输出密钥。
"""
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_gateway_vector_store import MemoryRepository
from app.settings import get_settings
from app.integrations.gateway import GatewayVectorStore
from app.modules.knowledge.service import KnowledgeBaseService


async def resolve_key(settings):
    if settings.vector_api_key:
        return settings.vector_api_key
    from sqlalchemy import select
    from app.infrastructure.database import get_session_factory
    from app.modules.credentials.models import GatewayUserCredential
    from app.modules.credentials.service import GatewayCredentialService
    async with get_session_factory()() as session:
        row = (await session.execute(select(GatewayUserCredential).order_by(
            GatewayUserCredential.updated_at.desc()).limit(1))).scalar_one_or_none()
        if row:
            return GatewayCredentialService._fernet().decrypt(row.encrypted_api_key.encode()).decode()
    if settings.context_api_key:
        return settings.context_api_key
    raise RuntimeError("No configured Gateway key")


def report(stage, **values):
    print(json.dumps({"stage": stage, **values}, ensure_ascii=False), flush=True)


async def main():
    settings = get_settings()
    key = await resolve_key(settings)
    name = "knowledge-smoke-" + uuid4().hex
    store = GatewayVectorStore(settings.vector_base_url, key, name, timeout=120)
    # 即使创建响应丢失，也按唯一名称尝试清理。
    cleanup_needed = True
    try:
        item = await store.create_collection(settings.embedding_model, description="临时父子索引联调，用后删除", chunk_size=1000)
        report("collection_created", collection=name, model=item.get("model"))
        parent = {"id": "probe-parent", "record_kind": "document", "content": "父块：退款需要订单号，审核通过后原路退回。",
                  "namespace": "smoke", "document_id": "probe", "version_id": "v1", "staged": True,
                  "metadata": {"source": "probe.md"}}
        await store.add_records([parent])
        await store.add_vectors([{**parent, "id": "probe-child", "record_kind": "vector", "parent_id": "probe-parent",
                                  "content": "退款需要订单号。"}])
        hidden = await store.fetch_records(["probe-parent", "probe-child"])
        assert set(hidden["missing_ids"]) == {"probe-parent", "probe-child"}, hidden
        report("staged_records_hidden")
        await store.publish_version("smoke", "probe", "v1")
        published = await store.fetch_records(["probe-parent", "probe-child"])
        assert len(published["records"]) == 2 and not published["missing_ids"]
        repeated = await store.publish_version("smoke", "probe", "v1")
        assert repeated.get("already_published") is True
        report("publish_and_idempotent_retry_passed")
        await store.delete_vectors(["probe-child"])
        await store.delete_vectors(["probe-parent"])

        repo = MemoryRepository()
        service = KnowledgeBaseService(store, repository=repo)
        content = "## 退款流程\n" + "申请退款时提交订单号，客服审核后原路退回款项。" * 12
        async def upsert(text):
            return await service.upsert_document(owner_user_id="smoke-owner", document_id="smoke-doc", title="退款指南",
                file_name="guide.md", content=text, metadata={"category": "smoke"},
                chunking_config={"strategy": "structure", "chunk_size": 100, "overlap": 10,
                    "hierarchy_enabled": True, "parent_chunk_size": 500, "parent_heading_level": 2})
        result = await upsert(content)
        assert not result["result"]["cleanup_pending"] and result["result"]["parents"] > 0
        old_groups = await repo.get_record_groups("smoke-doc", "smoke-owner", "")
        report("knowledge_parent_child_write_passed", children=result["result"]["chunks"], parents=result["result"]["parents"])
        hits = await service.search("申请退款需要提交什么？", owner_user_id="smoke-owner", top_k=5)
        report("search_diagnostics", hits=len(hits), statuses=[h["context_status"] for h in hits])
        assert hits and all(h["context_status"] == "available" for h in hits)
        assert all(h["parent_id"] and "订单号" in h["context_content"] for h in hits)
        report("knowledge_search_parent_fetch_passed", hits=len(hits))
        await upsert("## 新版退款流程\n退款时提交订单号及支付凭证，审核后原路退回。" * 3)
        obsolete = await store.fetch_records(old_groups["children"] + old_groups["parents"])
        assert not obsolete["records"]
        report("new_version_publish_old_records_removed")
        await service.delete_document("smoke-doc", owner_user_id="smoke-owner")
        assert not await store.similarity_search("退款", metadata={"owner_user_id": "smoke-owner"})
        report("document_children_then_parents_deleted")
    finally:
        if cleanup_needed:
            await store._request("DELETE", "/collections/" + name)
            assert not any(c.get("collection") == name for c in await store.list_collections())
            report("temporary_collection_deleted", collection=name)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        # 不打印服务器原始响应或凭据。
        report("failed", error_type=type(error).__name__, code=getattr(error, "code", None))
        raise SystemExit(1)
