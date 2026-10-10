"""知识库用例：本项目管理原文、切片和 Gateway 向量记录生命周期。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import unicodedata
import uuid
from pathlib import PurePosixPath
from typing import Any, Awaitable, Callable

from app.modules.knowledge.chunking import split_document_with_rule
from app.integrations.gateway.vector_store import GatewayVectorStore
from app.integrations.gateway.errors import GatewayVectorStoreError
from app.dependencies import build_vector_store
from app.modules.knowledge.repository import KnowledgeBaseRepository

CHUNK_OVERLAP = 120
logger = logging.getLogger(__name__)
EmbeddingProgress = Callable[[str, int, int | None], Awaitable[None]]


def normalize_virtual_path(directory: str, name: str) -> str:
    """Validate and normalize the user-facing virtual directory and file name."""

    normalized_name = unicodedata.normalize("NFC", name.strip())
    normalized_directory = unicodedata.normalize("NFC", directory.strip().replace("\\", "/"))
    if not normalized_name or normalized_name in {".", ".."} or "/" in normalized_name:
        raise ValueError("名称不能为空，也不能包含目录分隔符。")
    parts = [part.strip() for part in normalized_directory.split("/") if part.strip()]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("目录不能包含 . 或 .. 路径段。")
    path = str(PurePosixPath(*parts, normalized_name))
    if len(path) > 1000:
        raise ValueError("目录和名称合计不能超过 1000 个字符。")
    return path


def normalize_directory_path(path: str) -> str:
    """Normalize a relative directory path and reject traversal components."""
    normalized = unicodedata.normalize("NFC", path.strip().replace("\\", "/"))
    parts = [part.strip() for part in normalized.split("/") if part.strip()]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("目录不能包含 . 或 .. 路径段。")
    result = "/".join(parts)
    if len(result) > 1000:
        raise ValueError("目录路径不能超过 1000 个字符。")
    return result


def split_virtual_path(path: str) -> tuple[str, str]:
    """Return the normalized directory and filename from a relative file path."""
    normalized = normalize_directory_path(path)
    if not normalized:
        raise ValueError("文档路径不能为空。")
    parts = normalized.split("/")
    return "/".join(parts[:-1]), parts[-1]


def validate_chunking_rule(rule: dict[str, Any] | None, gateway_max: int) -> None:
    """Validate a fixed-length or literal-delimiter rule before it is persisted."""
    if rule is None:
        return
    strategy = rule.get("strategy")
    size = rule.get("chunk_size")
    overlap = rule.get("overlap", 0)
    if strategy not in {"structure", "fixed", "delimiter"}:
        raise ValueError("strategy 只能是 structure、fixed 或 delimiter。")
    if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= gateway_max:
        raise ValueError(f"chunk_size 必须在 1 到 Gateway 上限 {gateway_max} 之间。")
    if not isinstance(overlap, int) or isinstance(overlap, bool) or overlap < 0 or overlap >= size:
        raise ValueError("overlap 必须是非负整数且小于 chunk_size。")
    if strategy == "delimiter":
        delimiter = rule.get("delimiter")
        if not isinstance(delimiter, str) or not delimiter or len(delimiter) > 100:
            raise ValueError("自定义分隔符长度必须在 1 到 100 个字符之间。")
    hierarchy_enabled = rule.get("hierarchy_enabled", False)
    if not isinstance(hierarchy_enabled, bool):
        raise ValueError("hierarchy_enabled 必须是布尔值。")
    if hierarchy_enabled:
        parent_chunk_size = rule.get("parent_chunk_size", 4000)
        heading_level = rule.get("parent_heading_level", 2)
        if not isinstance(parent_chunk_size, int) or isinstance(parent_chunk_size, bool) or not 1 <= parent_chunk_size <= 20000:
            raise ValueError("parent_chunk_size 必须在 1 到 20000 之间。")
        if not isinstance(heading_level, int) or isinstance(heading_level, bool) or not 1 <= heading_level <= 6:
            raise ValueError("parent_heading_level 必须在 1 到 6 之间。")


def _prepare_vector_records(
    content: str, chunk_size: int, effective_rule: dict[str, Any], markdown: bool,
    stable_id: str, content_hash: str, document_metadata: dict[str, Any],
    title: str, path: str, target_fingerprint: str, version_id: str | None = None,
) -> list[dict[str, Any]]:
    """在线程中完成切片、稳定 ID 和逐条元数据生成；不访问数据库或 Gateway。"""
    pieces = split_document_with_rule(
        content, chunk_size=chunk_size, rule=effective_rule, markdown=markdown
    )
    if not pieces:
        raise ValueError(f"文档 {title} 没有可写入的文本")
    doc_hash = hashlib.sha256(stable_id.encode("utf-8")).hexdigest()[:32]
    rule_hash = hashlib.sha256(json.dumps(effective_rule, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    generation_hash = hashlib.sha256(json.dumps({
        "content": content_hash,
        "rule": effective_rule,
        "metadata": document_metadata,
        "title": title,
        "target": target_fingerprint,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    generation_hash = version_id or generation_hash
    records = []
    for index, piece in enumerate(pieces):
        parent_index = piece["parent_index"]
        parent_id = (
            f"{doc_hash}-{generation_hash}-p{parent_index:05d}"
            if parent_index is not None else None
        )
        vector_id = f"{doc_hash}-{generation_hash}-{rule_hash}-{index:06d}"
        piece_metadata = {
            **document_metadata,
            "document_id": stable_id,
            "source": path,
            "chunk_index": index,
            "chunk_count": len(pieces),
            "chunk_sha256": hashlib.sha256(piece["content"].encode("utf-8")).hexdigest(),
            "chunk_level": "child" if parent_id else "flat",
            "vector_record_id": vector_id,
        }
        if parent_id:
            piece_metadata["parent_id"] = parent_id
        records.append({
            "id": vector_id,
            "title": title,
            "content": piece["content"],
            "metadata": piece_metadata,
            "chunk_index": index,
            "parent_id": parent_id,
            "parent_content": piece["parent_content"],
            "record_kind": "vector",
            "namespace": "knowledge-platform:" + str(document_metadata["owner_user_id"]),
            "document_id": stable_id,
            "version_id": generation_hash,
            "staged": True,
        })
    return records


class KnowledgeBaseService:
    """为数据中台 API 提供目录、文档切片和 Gateway 向量生命周期操作。"""

    def __init__(self, vector_store: GatewayVectorStore | None = None, repository: KnowledgeBaseRepository | None = None) -> None:
        self.vector_store = vector_store or build_vector_store()
        self.repository = repository or KnowledgeBaseRepository()

    def _target_fingerprint(self, collection_status: dict[str, Any]) -> str:
        """Bind a confirmed generation to one Gateway collection incarnation."""

        self.vector_store.ensure_configured()
        collection_id = collection_status.get("id")
        # Older Gateway-compatible mocks may omit the collection UUID. A key
        # digest is a conservative fallback; the current Gateway returns an id.
        identity = (
            str(collection_id) if collection_id
            else hashlib.sha256(self.vector_store.api_key.encode("utf-8")).hexdigest()
        )
        payload = json.dumps({
            "base_url": self.vector_store.base_url,
            "collection": self.vector_store.collection,
            "identity": identity,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def status(self) -> dict[str, Any]:
        """读取配置集合状态；未配置时返回可诊断的未配置状态。"""

        try:
            self.vector_store.ensure_configured()
        except Exception:
            return {"configured": False, "exists": False}
        collections = await self.vector_store.list_collections()
        item = next(
            (entry for entry in collections if entry.get("collection") == self.vector_store.collection),
            None,
        )
        return {
            "configured": True,
            "exists": item is not None,
            "id": item.get("id") if item else None,
            "target_fingerprint": self._target_fingerprint(item) if item else None,
            "collection": self.vector_store.collection,
            "model": item.get("model") if item else None,
            "description": item.get("description") if item else None,
            "created_at": item.get("created_at") if item else None,
            "collection_chunk_overlap": item.get("chunk_overlap") if item else None,
            "vector_dimensions": item.get("vector_dimensions") if item else None,
            "chunk_size": item.get("chunk_size") if item else None,
            "chunk_overlap": min(CHUNK_OVERLAP, int(item.get("chunk_size", 1000)) // 5) if item else None,
        }

    async def initialize(self, *, description: str, model: str, chunk_size: int) -> dict[str, Any]:
        """创建集合或校验已有集合；Embedding 模型在初始化时指定。"""
        return await self.vector_store.ensure_collection(
            model=model,
            description=description,
            chunk_size=chunk_size,
        )

    async def save_document_drafts(
        self, documents: list[dict[str, Any]], *, owner_user_id: str
    ) -> list[dict[str, Any]]:
        """Save uploaded or edited source text locally; this operation never calls vector upsert."""

        # Saving source text must remain local and fast. The worker checks the
        # collection's actual single-record limit before it starts chunking.
        gateway_max = 1000
        results: list[dict[str, Any]] = []
        submitted_paths: set[str] = set()
        for document in documents:
            title = str(document["title"])
            content = str(document["content"])
            raw_id = document.get("document_id")
            raw_file_name = document.get("file_name")
            requested_name = document.get("name") or raw_file_name or title
            requested_source = str(document.get("source_type") or ("file" if raw_file_name else "text"))
            if requested_source not in {"file", "text"}:
                raise ValueError("source_type 只能是 file 或 text。")
            path = normalize_virtual_path(str(document.get("directory") or ""), str(requested_name))
            if path in submitted_paths:
                raise ValueError(f"批次中包含重复路径：{path}")
            submitted_paths.add(path)
            existing = await self.repository.get_document_by_path(path, owner_user_id)
            if (
                existing
                and isinstance(raw_id, str)
                and raw_id.strip()
                and existing["document_id"] != raw_id.strip()
            ):
                raise ValueError(f"目标路径已存在其他文档：{path}")
            if not existing and isinstance(raw_id, str) and raw_id.strip():
                owned = await self.repository.get_document_by_id(raw_id.strip(), owner_user_id)
                if not owned and await self.repository.document_exists(raw_id.strip()):
                    raise PermissionError("DOCUMENT_NOT_OWNED")
            stable_id = (
                str(existing["document_id"])
                if existing
                else (raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else str(uuid.uuid4()))
            )
            normalized_content = unicodedata.normalize(
                "NFC", content.replace("\r\n", "\n").replace("\r", "\n")
            ).strip()
            if not normalized_content:
                raise ValueError(f"文档 {title} 没有可保存的文本。")
            content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
            file_name = str(requested_name) if requested_source == "file" else None
            metadata = dict(document.get("metadata") or {})
            metadata.update({
                "metadata_schema_version": 1,
                "app_id": "knowledge-platform",
                "record_type": "knowledge_chunk",
                "owner_user_id": owner_user_id,
                "source_type": requested_source,
                "path": path,
                "source": path,
                "name": str(requested_name),
                "content_sha256": content_hash,
            })
            if file_name:
                metadata.update({
                    "file_name": file_name,
                    "file_extension": PurePosixPath(file_name).suffix.lower().lstrip("."),
                })
            else:
                metadata.pop("file_name", None)
                metadata.pop("file_extension", None)
            file_rule = document.get("chunking_config")
            if "chunking_config" not in document and existing:
                file_rule = existing.get("chunking_config")
            effective_rule = await self.repository.get_effective_rule(path, owner_user_id, file_rule)
            effective_rule = effective_rule or {
                "strategy": "structure",
                "chunk_size": min(gateway_max, 1000),
                "overlap": min(CHUNK_OVERLAP, min(gateway_max, 1000) // 5),
            }
            validate_chunking_rule(effective_rule, max(gateway_max, 6000))
            metadata["chunking_rule"] = effective_rule
            await self.repository.save_document_draft(
                document_id=stable_id,
                owner_user_id=owner_user_id,
                title=title,
                path=path,
                content=normalized_content,
                file_name=file_name,
                content_sha256=content_hash,
                metadata=metadata,
                chunking_config=file_rule,
            )
            results.append({"id": stable_id, "result": {"action": "draft_saved"}})
        return results

    async def upsert_document(
        self,
        *,
        owner_user_id: str,
        document_id: str | None,
        title: str,
        content: str,
        metadata: dict[str, Any],
        file_name: str | None = None,
        directory: str = "",
        name: str | None = None,
        source_type: str | None = None,
        chunking_config: dict[str, Any] | None = None,
        progress: EmbeddingProgress | None = None,
    ) -> dict[str, Any]:
        """按虚拟路径新增或更新目录项，切片后逐条写入 Gateway。"""

        results = await self.upsert_documents(
            [{"document_id": document_id, "file_name": file_name, "title": title, "content": content,
              "metadata": metadata, "directory": directory, "name": name, "source_type": source_type,
              "chunking_config": chunking_config}],
            owner_user_id=owner_user_id, progress=progress,
        )
        return results[0]

    async def upsert_documents(
        self, documents: list[dict[str, Any]], *, owner_user_id: str,
        progress: EmbeddingProgress | None = None,
    ) -> list[dict[str, Any]]:
        """Serialize per-path upserts before touching local or Gateway state.

        The lock is PostgreSQL-backed in production, so it also coordinates
        multiple Uvicorn workers. Repositories used by isolated callers may omit
        the lock capability and continue to use the same application workflow.
        """

        prepared: list[tuple[str, dict[str, Any]]] = []
        seen_paths: set[str] = set()
        for document in documents:
            requested_name = document.get("name") or document.get("file_name") or document.get("title")
            path = normalize_virtual_path(
                str(document.get("directory") or ""), str(requested_name or "")
            )
            if path in seen_paths:
                raise ValueError(f"批次中包含重复路径：{path}")
            seen_paths.add(path)
            prepared.append((path, document))

        results: list[dict[str, Any]] = []
        lock_factory = getattr(self.repository, "document_sync_lock", None)
        for path, document in prepared:
            if lock_factory is None:
                results.extend(await self._upsert_documents_unlocked([document], owner_user_id=owner_user_id, progress=progress))
                continue
            document_id = document.get("document_id")
            existing = None
            if not (isinstance(document_id, str) and document_id.strip()):
                existing = await self.repository.get_document_by_path(path, owner_user_id)
            lock_key = (
                f"document:{document_id.strip()}"
                if isinstance(document_id, str) and document_id.strip()
                else f"document:{existing['document_id']}"
                if existing
                else f"path:{path}"
            )
            async with lock_factory(owner_user_id, lock_key):
                results.extend(await self._upsert_documents_unlocked([document], owner_user_id=owner_user_id, progress=progress))
        return results

    async def _upsert_documents_unlocked(
        self, documents: list[dict[str, Any]], *, owner_user_id: str,
        progress: EmbeddingProgress | None = None,
    ) -> list[dict[str, Any]]:
        """Persist document mappings and write split records while the document lock is held."""

        status = await self.status()
        if not status.get("exists"):
            raise ValueError("知识库集合尚未创建")
        target_fingerprint = self._target_fingerprint(status)
        chunk_size = int(status.get("chunk_size") or 1000)
        results: list[dict[str, Any]] = []
        submitted_paths: set[str] = set()
        for document in documents:
            if progress:
                await progress("checking", 0, None)
            title = str(document["title"])
            content = str(document["content"])
            raw_id = document.get("document_id")
            raw_file_name = document.get("file_name")
            requested_name = document.get("name") or raw_file_name or title
            requested_source = str(document.get("source_type") or ("file" if raw_file_name else "text"))
            if requested_source not in {"file", "text"}:
                raise ValueError("source_type 只能是 file 或 text。")
            path = normalize_virtual_path(str(document.get("directory") or ""), str(requested_name))
            if path in submitted_paths:
                raise ValueError(f"批次中包含重复路径：{path}")
            submitted_paths.add(path)
            file_name = str(requested_name) if requested_source == "file" else None
            existing = await self.repository.get_document_by_path(path, owner_user_id)
            if (
                existing
                and isinstance(raw_id, str)
                and raw_id.strip()
                and existing["document_id"] != raw_id.strip()
            ):
                raise ValueError(f"目标路径已存在其他文档：{path}")
            if not existing and isinstance(raw_id, str) and raw_id.strip():
                owned = await self.repository.get_document_by_id(raw_id.strip(), owner_user_id)
                if not owned and await self.repository.document_exists(raw_id.strip()):
                    raise PermissionError("DOCUMENT_NOT_OWNED")
            stable_id = (
                str(existing["document_id"])
                if existing
                else (raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else str(uuid.uuid4()))
            )
            normalized_content = unicodedata.normalize("NFC", content.replace("\r\n", "\n").replace("\r", "\n")).strip()
            content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
            document_metadata = dict(document.get("metadata") or {})
            document_metadata.update({
                "metadata_schema_version": 1,
                "app_id": "knowledge-platform",
                "record_type": "knowledge_chunk",
                "owner_user_id": owner_user_id,
                "source_type": requested_source,
                "path": path,
                "source": path,
                "name": str(requested_name),
                "content_sha256": content_hash,
            })
            if file_name:
                document_metadata.update({
                    "file_name": file_name,
                    "file_extension": PurePosixPath(file_name).suffix.lower().lstrip("."),
                })
            else:
                document_metadata.pop("file_name", None)
                document_metadata.pop("file_extension", None)
            file_rule = document.get("chunking_config")
            if "chunking_config" not in document and existing:
                file_rule = existing.get("chunking_config")
            effective_rule = await self.repository.get_effective_rule(path, owner_user_id, file_rule)
            effective_rule = effective_rule or {
                "strategy": "structure",
                "chunk_size": chunk_size,
                "overlap": min(CHUNK_OVERLAP, chunk_size // 5),
            }
            validate_chunking_rule(effective_rule, chunk_size)
            document_metadata["chunking_rule"] = effective_rule
            document_metadata["vector_storage_version"] = 2
            active_reader = getattr(self.repository, "get_active_version", None)
            active = await active_reader(stable_id, owner_user_id) if active_reader else None
            comparison = active or (existing if existing and existing.get("sync_status") == "ready" else None)
            if (
                comparison
                and comparison["content_sha256"] == content_hash
                and comparison["metadata"] == document_metadata
                and comparison["title"] == title
                and comparison.get("embedded_target_fingerprint") == target_fingerprint
                and (active is None or bool(active["active_ids"]))
                and not comparison.get("has_inactive", False)
            ):
                if active and active["active_ids"]:
                    await self.repository.finish_sync(stable_id, active["active_ids"], target_fingerprint, keep_inactive=True)
                results.append({
                    "id": stable_id,
                    "result": {"vectors": 0, "chunks": existing.get("chunk_count", 0), "action": "unchanged"},
                })
                continue
            content = normalized_content
            markdown = PurePosixPath(path).suffix.lower() == ".md"
            if progress:
                await progress("chunking", 0, None)
            signature = hashlib.sha256(json.dumps({
                "content": content_hash, "rule": effective_rule, "metadata": document_metadata,
                "title": title, "target": target_fingerprint,
            }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            plan = await self.repository.prepare_embedding_plan(
                stable_id, owner_user_id, signature, title, path,
            )
            records = await asyncio.to_thread(
                _prepare_vector_records, content, chunk_size, effective_rule, markdown,
                stable_id, content_hash, document_metadata, title, path, target_fingerprint,
                plan["version_id"],
            )
            old_groups = await self.repository.get_record_groups(stable_id, owner_user_id, target_fingerprint)
            await self.repository.stage_document(
                document_id=stable_id,
                owner_user_id=owner_user_id,
                title=title,
                path=path,
                content=content,
                file_name=file_name,
                content_sha256=content_hash,
                metadata=document_metadata,
                chunking_config=file_rule,
                target_fingerprint=target_fingerprint,
                chunks=records,
            )
            target_ids = [record["id"] for record in records]
            parents = {}
            for record in records:
                if record["parent_id"]:
                    parents[record["parent_id"]] = {
                        "id": record["parent_id"], "title": title,
                        "record_kind": "document", "content": record["parent_content"],
                        "namespace": record["namespace"], "document_id": stable_id,
                        "version_id": plan["version_id"], "staged": True,
                        "metadata": {**document_metadata, "document_id": stable_id, "record_type": "knowledge_parent",
                                     "parent_id": record["parent_id"]},
                    }
            publish_attempted = False
            try:
                if parents:
                    if progress:
                        await progress("saving_parents", 0, len(records))
                    await self.vector_store.add_records(list(parents.values()))
                if progress:
                    await progress("embedding", 0, len(records))
                async def batch_progress(completed: int, total: int) -> None:
                    if progress:
                        await progress("embedding", completed, total)

                response = await self.vector_store.add_vectors([
                    {key: record[key] for key in (
                        "id", "title", "content", "metadata", "record_kind", "parent_id",
                        "namespace", "document_id", "version_id", "staged",
                    )} for record in records
                ], on_batch=batch_progress)
                written = len(response["upserted"])
                if progress:
                    await progress("publishing", written, len(records))
                await self.repository.set_embedding_plan_state(stable_id, "publishing")
                publish_attempted = True
                await self.vector_store.publish_version(records[0]["namespace"], stable_id, plan["version_id"])
                # 外部发布后先提交本地活动代，旧数据清理失败不能回滚已发布版本。
                await self.repository.finish_sync(stable_id, target_ids, target_fingerprint, keep_inactive=True)
            except Exception as error:
                # 不自动删除失败代：publish 超时/取消可能已生效，确定性 ID 支持重试。
                code = "VECTOR_PUBLICATION_PENDING" if publish_attempted else getattr(error, "code", None)
                await self.repository.mark_failed(stable_id, code)
                if publish_attempted:
                    raise GatewayVectorStoreError(
                        "VECTOR_PUBLICATION_PENDING", getattr(error, "status_code", None),
                        gateway_trace_id=getattr(error, "gateway_trace_id", None),
                    ) from error
                raise
            cleanup_pending = False
            try:
                if progress:
                    await progress("deleting_old", written, len(records))
                stale_ids = [value for value in old_groups["children"] if value not in set(target_ids)]
                stale_parents = [value for value in old_groups["parents"] if value not in parents]
                if stale_ids:
                    await self.vector_store.delete_vectors(stale_ids)
                if stale_parents:
                    await self.vector_store.delete_vectors(stale_parents)
                await self.repository.remove_inactive_records(stable_id, target_fingerprint)
            except Exception:
                cleanup_pending = True
                await self.repository.mark_cleanup_pending(stable_id)
                if progress:
                    await progress("cleanup_pending", written, len(records))
            results.append({"id": stable_id, "result": {
                "vectors": written, "chunks": len(target_ids),
                "action": "updated" if existing else "created",
                "cleanup_pending": cleanup_pending, "parents": len(parents),
            }})
        return results

    async def delete_document(self, document_id: str, *, owner_user_id: str) -> dict[str, Any]:
        """Delete a document while holding the same per-document lock as reindex."""

        lock_factory = getattr(self.repository, "document_sync_lock", None)
        if lock_factory is None:
            return await self._delete_document_unlocked(document_id, owner_user_id=owner_user_id)
        async with lock_factory(owner_user_id, f"document:{document_id}"):
            return await self._delete_document_unlocked(document_id, owner_user_id=owner_user_id)

    async def _delete_document_unlocked(
        self, document_id: str, *, owner_user_id: str
    ) -> dict[str, Any]:
        """删除文档关联的所有切片向量；Gateway 成功后再删除本地原文和映射。"""

        active_reader = getattr(self.repository, "get_active_version", None)
        active = await active_reader(document_id, owner_user_id) if active_reader else None
        if active and active.get("active_ids"):
            current_target = self._target_fingerprint(await self.status())
            if active.get("embedded_target_fingerprint") != current_target:
                raise GatewayVectorStoreError("VECTOR_DOCUMENT_TARGET_MISMATCH")
        detail = await self.repository.get_document_detail(document_id, owner_user_id)
        if detail and detail.get("sync_error_code") == "VECTOR_PUBLICATION_PENDING":
            raise GatewayVectorStoreError("VECTOR_PUBLICATION_PENDING")
        groups = await self.repository.get_record_groups(
            document_id, owner_user_id, self._target_fingerprint(await self.status()),
        )
        owned_ids = groups["children"]
        if not owned_ids and not await self.repository.document_exists(document_id):
            return {"deleted": 0}
        if not owned_ids:
            # Do not delete a same-ID vector if the local owner check failed.
            owner_exists = await self.repository.get_document_by_id(document_id, owner_user_id)
            if owner_exists is None:
                return {"deleted": 0}
            await self.vector_store.delete([document_id])
            await self.repository.delete_local_document(document_id, owner_user_id)
            return {"deleted": 0}
        result = await self.vector_store.delete(owned_ids)
        if groups["parents"]:
            await self.vector_store.delete(groups["parents"])
        await self.repository.delete_local_document(document_id, owner_user_id)
        return result

    async def list_documents(self, *, owner_user_id: str) -> list[dict[str, Any]]:
        """列出已登记文档以支持刷新后的更新和删除。"""

        return await self.repository.list_documents(owner_user_id)

    async def get_tree(self, *, owner_user_id: str) -> dict[str, Any]:
        """Return explicit and implicit directories with document summaries."""
        documents = await self.repository.list_documents(owner_user_id)
        explicit = await self.repository.list_directories(owner_user_id)
        paths = {item["path"]: item.get("chunking_config") for item in explicit}
        paths.setdefault("", None)
        for document in documents:
            parts = str(document.get("path") or "").split("/")[:-1]
            for index in range(1, len(parts) + 1):
                paths.setdefault("/".join(parts[:index]), None)
        directories = [{"path": path, "chunking_config": rule} for path, rule in sorted(paths.items())]
        return {"directories": directories, "documents": documents}

    async def get_document(self, document_id: str, *, owner_user_id: str) -> dict[str, Any] | None:
        """Read an owned document for the workbench editor."""
        return await self.repository.get_document_detail(document_id, owner_user_id)

    async def create_directory(self, path: str, *, owner_user_id: str) -> dict[str, Any]:
        """Create a virtual directory and its parent nodes."""
        normalized = normalize_directory_path(path)
        if not normalized:
            raise ValueError("不能创建根目录。")
        if await self.repository.get_document_by_path(normalized, owner_user_id):
            raise ValueError("同一路径已存在文件，不能同时创建同名目录。")
        await self.repository.create_empty_directory(normalized, owner_user_id)
        return {"path": normalized}

    async def delete_directory(self, path: str, *, owner_user_id: str) -> bool:
        normalized = normalize_directory_path(path)
        if not normalized:
            raise ValueError("不能删除根目录。")
        return await self.repository.delete_empty_directory(normalized, owner_user_id)

    async def set_directory_rule(
        self, path: str, rule: dict[str, Any] | None, *, owner_user_id: str
    ) -> dict[str, Any]:
        """Save a directory override and identify descendants that require re-embedding."""
        normalized = normalize_directory_path(path)
        status = await self.status()
        max_size = int(status.get("chunk_size") or 1000)
        validate_chunking_rule(rule, max_size)
        docs = await self.repository.list_document_details(owner_user_id)
        affected = [
            doc for doc in docs
            if not normalized or str(doc["path"]).startswith(normalized + "/")
        ]
        previous = {
            doc["document_id"]: await self.repository.get_effective_rule(
                doc["path"], owner_user_id, doc.get("chunking_config")
            ) for doc in affected
        }
        await self.repository.set_directory_rule(normalized, rule, owner_user_id)
        outcomes: list[dict[str, Any]] = []
        for doc in affected:
            new_rule = await self.repository.get_effective_rule(
                doc["path"], owner_user_id, doc.get("chunking_config")
            )
            if previous[doc["document_id"]] == new_rule:
                continue
            if not doc.get("content"):
                await self.repository.mark_needs_upload(doc["document_id"])
                outcomes.append({"document_id": doc["document_id"], "status": "needs_upload"})
                continue
            outcomes.append({"document_id": doc["document_id"], "status": "pending"})
        return {"path": normalized, "affected": len(outcomes), "documents": outcomes}

    async def set_document_rule(
        self, document_id: str, rule: dict[str, Any] | None, *, owner_user_id: str
    ) -> dict[str, Any]:
        doc = await self.get_document(document_id, owner_user_id=owner_user_id)
        if doc is None:
            raise PermissionError("DOCUMENT_NOT_OWNED")
        if not doc.get("content"):
            raise ValueError("此旧文档没有保存在本地的原文，请重新上传后再设置切片规则。")
        status = await self.status()
        validate_chunking_rule(rule, int(status.get("chunk_size") or 1000))
        directory, name = split_virtual_path(doc["path"])
        await self.save_document_drafts([{
            "document_id": document_id, "title": name, "content": doc["content"],
            "metadata": doc["metadata"], "directory": directory, "name": name,
            "source_type": str(doc["metadata"].get("source_type", "file" if doc["metadata"].get("file_name") else "text")),
            "file_name": doc["metadata"].get("file_name"), "chunking_config": rule,
        }], owner_user_id=owner_user_id)
        return {"id": document_id, "result": {"action": "draft_saved"}}

    async def move_document(
        self, document_id: str, target_path: str, *, owner_user_id: str
    ) -> dict[str, Any]:
        doc = await self.get_document(document_id, owner_user_id=owner_user_id)
        if doc is None:
            raise PermissionError("DOCUMENT_NOT_OWNED")
        if not doc.get("content"):
            raise ValueError("此旧文档没有保存在本地的原文，请重新上传后再移动。")
        directory, name = split_virtual_path(target_path)
        normalized = normalize_virtual_path(directory, name)
        collision = await self.repository.get_document_by_path(normalized, owner_user_id)
        if collision and collision["document_id"] != document_id:
            raise ValueError("目标路径已存在其他文档。")
        metadata = dict(doc["metadata"])
        await self.save_document_drafts([{
            "document_id": document_id, "title": name, "content": doc["content"],
            "metadata": metadata, "directory": directory, "name": name,
            "source_type": str(metadata.get("source_type", "file" if metadata.get("file_name") else "text")),
            "file_name": name if metadata.get("source_type") == "file" else None,
            "chunking_config": doc.get("chunking_config"),
        }], owner_user_id=owner_user_id)
        return {"id": document_id, "result": {"action": "draft_saved"}}

    async def reindex_document(
        self, document_id: str, *, owner_user_id: str, progress: EmbeddingProgress | None = None
    ) -> dict[str, Any]:
        doc = await self.get_document(document_id, owner_user_id=owner_user_id)
        if doc is None:
            raise PermissionError("DOCUMENT_NOT_OWNED")
        if not doc.get("content"):
            raise ValueError("此旧文档没有保存在本地的原文，请重新上传后再嵌入。")
        directory, name = split_virtual_path(doc["path"])
        return (await self.upsert_document(
            owner_user_id=owner_user_id, document_id=document_id, title=name, content=doc["content"],
            metadata=doc["metadata"], directory=directory, name=name,
            source_type=str(doc["metadata"].get("source_type", "file" if doc["metadata"].get("file_name") else "text")),
            file_name=doc["metadata"].get("file_name"), chunking_config=doc.get("chunking_config"),
            progress=progress,
        ))

    async def search(
        self, query: str, *, owner_user_id: str, top_k: int = 5,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """执行 Gateway 相似度查询并返回项目使用的统一检索结果结构。"""

        filters = dict(metadata or {})
        filters.update({"app_id": "knowledge-platform", "record_type": "knowledge_chunk", "owner_user_id": owner_user_id})
        results = await self.vector_store.similarity_search(query, k=min(20, max(top_k * 4, top_k)), metadata=filters)
        active_reader = getattr(self.repository, "get_active_chunk_signatures", None)
        if active_reader:
            document_ids = [
                str(item.get("metadata", {}).get("document_id", ""))
                for item in results if isinstance(item.get("metadata"), dict)
            ]
            target_status = await self.status()
            target_fingerprint = self._target_fingerprint(target_status) if target_status.get("exists") else ""
            active_ids, active_signatures = await active_reader(owner_user_id, document_ids, target_fingerprint)
            def is_active_result(item: dict[str, Any]) -> bool:
                item_metadata = item.get("metadata")
                if not isinstance(item_metadata, dict):
                    return False
                if item_metadata.get("vector_storage_version") == 2:
                    return True
                vector_id = item_metadata.get("vector_record_id")
                if vector_id is not None:
                    return isinstance(vector_id, str) and vector_id in active_ids
                chunk_index = item_metadata.get("chunk_index")
                chunk_hash = item_metadata.get("chunk_sha256")
                if not isinstance(chunk_index, int):
                    return False
                if not isinstance(chunk_hash, str):
                    content = item.get("content", item.get("text"))
                    if not isinstance(content, str):
                        return False
                    chunk_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                return (str(item_metadata.get("document_id", "")), chunk_index, chunk_hash) in active_signatures

            results = [item for item in results if is_active_result(item)][:top_k]
        normalized: list[dict[str, Any]] = []
        parent_ids = list(dict.fromkeys(
            str(item.get("parent_id") or item.get("metadata", {}).get("parent_id"))
            for item in results
            if item.get("parent_id") or (isinstance(item.get("metadata"), dict) and item["metadata"].get("parent_id"))
        ))
        parent_contexts = {}
        if parent_ids:
            try:
                fetched = await self.vector_store.fetch_records(parent_ids)
                for parent in fetched["records"]:
                    metadata = parent.get("metadata", {})
                    if (parent.get("record_kind") == "document" and isinstance(metadata, dict)
                            and metadata.get("owner_user_id") == owner_user_id
                            and metadata.get("record_type") == "knowledge_parent"):
                        parent_contexts[(str(metadata.get("document_id", "")), parent["id"])] = parent["content"]
            except GatewayVectorStoreError:
                logger.warning("parent_context_fetch_failed")
        for item in results:
            item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            document_id = str(item_metadata.get("document_id", item.get("document_id", item.get("external_id", ""))))
            parent_id = str(item.get("parent_id") or item_metadata.get("parent_id", "")) or None
            content = str(item.get("content", item.get("text", "")))
            normalized.append(
                {
                    "chunk_id": str(item.get("chunk_id", item.get("id", ""))),
                    "document_id": document_id,
                    "title": str(item.get("title", "")),
                    "content": content,
                    "context_content": parent_contexts.get((document_id, parent_id), content) if parent_id else content,
                    "parent_id": parent_id,
                    "context_status": "available" if not parent_id or (document_id, parent_id) in parent_contexts else "missing",
                    "score": item.get("score", item.get("similarity")),
                    "metadata": item_metadata,
                }
            )
        return normalized

def get_knowledge_base_service(*, api_key: str | None = None, collection: str | None = None) -> KnowledgeBaseService:
    """装配业务服务，Gateway 的配置只由应用装配层读取。"""
    return KnowledgeBaseService(vector_store=build_vector_store(api_key, collection=collection))
