"""知识库目录、原文、切片规则及 Gateway 向量 ID 映射。"""

from __future__ import annotations

import hashlib
import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Any, AsyncIterator
from uuid import uuid4

from sqlalchemy import and_, case, func, or_, select, text

from services.auth.database import get_session_factory
from services.rag.models import KnowledgeChunk, KnowledgeDirectory, KnowledgeDocument, KnowledgeEmbeddingJob
from services.rag.activity_log import add_activity


class DocumentSyncLockTimeout(RuntimeError):
    code = "DOCUMENT_SYNC_LOCK_TIMEOUT"


class KnowledgeBaseRepository:
    """持久化虚拟路径、内容哈希、切片序号与向量 ID 映射。"""

    @asynccontextmanager
    async def document_sync_lock(self, owner_user_id: str, resource_key: str) -> AsyncIterator[None]:
        """Serialize one document's full sync lifecycle across app workers.

        PostgreSQL session advisory locks remain held while Gateway requests run,
        preventing concurrent retries from deleting one another's external IDs.
        """

        factory = get_session_factory()
        engine = factory.kw["bind"]
        digest = hashlib.sha256(f"{owner_user_id}\0{resource_key}".encode("utf-8")).digest()
        lock_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
        async with engine.connect() as connection:
            deadline = time.monotonic() + 30
            while True:
                acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id})
                await connection.commit()
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise DocumentSyncLockTimeout("文档同步锁等待超时")
                await asyncio.sleep(1)
            try:
                yield
            finally:
                await connection.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
                await connection.commit()

    async def stage_document(
        self,
        *,
        document_id: str,
        owner_user_id: str,
        title: str,
        path: str,
        content: str,
        file_name: str | None,
        content_sha256: str,
        metadata: dict[str, Any],
        chunking_config: dict[str, Any] | None,
        target_fingerprint: str,
        chunks: list[dict[str, Any]],
    ) -> list[str]:
        """持久化可编辑原文、目标规则和新一代切片映射。"""

        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                document = await session.get(KnowledgeDocument, document_id, with_for_update=True)
                if document is not None and document.owner_user_id != owner_user_id:
                    raise PermissionError("DOCUMENT_NOT_OWNED")
                old_ids = list((await session.scalars(
                    select(KnowledgeChunk.id).where(
                        KnowledgeChunk.document_id == document_id,
                        or_(
                            KnowledgeChunk.target_fingerprint == target_fingerprint,
                            KnowledgeChunk.target_fingerprint.is_(None),
                        ),
                    )
                )).all())
                if document is None:
                    document = KnowledgeDocument(
                        id=document_id, title=title, path=path, content=None, file_name=file_name,
                        content_sha256=content_sha256, metadata_json=metadata, sync_status="syncing",
                        owner_user_id=owner_user_id, chunking_config=chunking_config,
                    )
                    session.add(document)
                document.pending_document = {
                    "title": title,
                    "path": path,
                    "content": content,
                    "file_name": file_name,
                    "content_sha256": content_sha256,
                    "metadata": metadata,
                    "chunking_config": chunking_config,
                }
                document.sync_status = "syncing"
                document.sync_error_code = None
                for chunk in chunks:
                    row = await session.get(KnowledgeChunk, chunk["id"])
                    if row is None:
                        row = KnowledgeChunk(id=chunk["id"], document_id=document_id)
                        session.add(row)
                    row.chunk_index = chunk["chunk_index"]
                    row.content = chunk["content"]
                    row.parent_id = chunk.get("parent_id")
                    row.parent_content = chunk.get("parent_content")
                    row.metadata_json = chunk["metadata"]
                    row.target_fingerprint = target_fingerprint
                    row.is_active = False
            return old_ids

    async def save_document_draft(
        self,
        *,
        document_id: str,
        owner_user_id: str,
        title: str,
        path: str,
        content: str,
        file_name: str | None,
        content_sha256: str,
        metadata: dict[str, Any],
        chunking_config: dict[str, Any] | None,
    ) -> None:
        """Persist source edits without creating or changing Gateway vectors."""

        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                document = await session.get(KnowledgeDocument, document_id, with_for_update=True)
                if document is not None and document.owner_user_id != owner_user_id:
                    raise PermissionError("DOCUMENT_NOT_OWNED")
                if document is not None and await session.scalar(select(KnowledgeEmbeddingJob.id).where(
                    KnowledgeEmbeddingJob.document_id == document_id,
                    KnowledgeEmbeddingJob.status.in_(("queued", "running")),
                ).limit(1)):
                    raise ValueError("此文件正在排队或嵌入中，请完成后再编辑。")
                if document is None:
                    document = KnowledgeDocument(
                        id=document_id,
                        title=title,
                        path=path,
                        content=None,
                        file_name=file_name,
                        content_sha256=content_sha256,
                        metadata_json=metadata,
                        sync_status="pending",
                        owner_user_id=owner_user_id,
                        chunking_config=chunking_config,
                    )
                    session.add(document)
                document.pending_document = {
                    "title": title,
                    "path": path,
                    "content": content,
                    "file_name": file_name,
                    "content_sha256": content_sha256,
                    "metadata": metadata,
                    "chunking_config": chunking_config,
                }
                document.sync_status = "pending"
                document.sync_error_code = None
                add_activity(session, owner_user_id=owner_user_id, document_id=document_id,
                             path=path, event_type="document_draft_saved", status="success")

    async def finish_sync(
        self, document_id: str, active_ids: list[str], target_fingerprint: str
    ) -> None:
        """标记目标切片生效并清除已从 Gateway 删除的旧映射。"""

        factory = get_session_factory()
        active = set(active_ids)
        async with factory() as session:
            async with session.begin():
                document = await session.get(KnowledgeDocument, document_id, with_for_update=True)
                if document is None:
                    return
                rows = list((await session.scalars(
                    select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
                )).all())
                for row in rows:
                    if row.id in active:
                        row.is_active = True
                    else:
                        await session.delete(row)
                pending = document.pending_document or {}
                if pending:
                    document.title = pending.get("title", document.title)
                    document.path = pending.get("path", document.path)
                    document.content = pending.get("content")
                    document.file_name = pending.get("file_name")
                    document.content_sha256 = pending.get("content_sha256", document.content_sha256)
                    document.metadata_json = pending.get("metadata", document.metadata_json)
                    document.chunking_config = pending.get("chunking_config")
                    document.pending_document = None
                document.sync_status = "ready"
                document.sync_error_code = None
                document.embedded_target_fingerprint = target_fingerprint

    async def mark_failed(self, document_id: str, error_code: str | None = None) -> None:
        """保留旧活动版本和失败代的 ID 映射，供后续重试/删除清理。"""

        factory = get_session_factory()
        async with factory() as session:
            document = await session.get(KnowledgeDocument, document_id)
            if document is not None:
                document.sync_status = "failed"
                document.sync_error_code = error_code
                await session.commit()

    async def mark_needs_upload(self, document_id: str) -> None:
        """Mark a legacy directory entry whose original text is unavailable locally."""
        factory = get_session_factory()
        async with factory() as session:
            document = await session.get(KnowledgeDocument, document_id)
            if document is not None:
                document.sync_status = "needs_upload"
                document.sync_error_code = "SOURCE_REQUIRED"
                await session.commit()

    async def list_documents(self, owner_user_id: str) -> list[dict[str, Any]]:
        """列出本项目已登记文档，不返回正文。"""

        factory = get_session_factory()
        async with factory() as session:
            # The explorer needs summaries, not source/chunk/parent text payloads.
            chunk_counts = (
                select(KnowledgeChunk.document_id, func.count().label("chunk_count"))
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(KnowledgeDocument.owner_user_id == owner_user_id, KnowledgeChunk.is_active.is_(True))
                .group_by(KnowledgeChunk.document_id).subquery()
            )
            document = KnowledgeDocument
            columns = [column for column in document.__table__.columns
                       if column.name not in {"content", "pending_document"}]
            rows = (await session.execute(
                select(
                    *columns,
                    (func.length(document.content) > 0).label("content"),
                    case(
                        (func.jsonb_typeof(document.pending_document) == "object",
                         document.pending_document.op("-")("content")),
                        else_=None,
                    ).label("pending_document"),
                    (func.length(func.coalesce(document.pending_document["content"].astext,
                                              document.content)) > 0).label("has_current_content"),
                    func.coalesce(chunk_counts.c.chunk_count, 0).label("chunk_count"),
                )
                .outerjoin(chunk_counts, document.id == chunk_counts.c.document_id)
                .where(document.owner_user_id == owner_user_id)
                .order_by(document.updated_at.desc())
            )).all()
            result = []
            for row in rows:
                current = row.pending_document or {}
                path = current.get("path", row.path)
                metadata = current.get("metadata", row.metadata_json) or {}
                file_name = current.get("file_name", row.file_name)
                result.append({
                    "document_id": row.id,
                    "title": current.get("title", row.title),
                    "path": path,
                    "name": PurePosixPath(path).name if path else file_name or row.title,
                    "directory": str(PurePosixPath(path).parent) if path and str(PurePosixPath(path).parent) != "." else "",
                    "source_type": metadata.get("source_type", "file" if file_name else "text"),
                    "file_name": file_name,
                    "content_sha256": current.get("content_sha256", row.content_sha256),
                    "sync_status": row.sync_status,
                    "sync_error_code": row.sync_error_code,
                    "chunk_count": row.chunk_count,
                    "has_content": bool(row.has_current_content),
                    "has_previous_version": bool(row.content),
                    "chunking_config": current.get("chunking_config", row.chunking_config),
                    "metadata": metadata,
                    "embedded_target_fingerprint": row.embedded_target_fingerprint,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                })
            return result

    async def get_document_by_path(self, path: str, owner_user_id: str) -> dict[str, Any] | None:
        """按当前知识库的规范化虚拟路径读取目录项，用于稳定识别更新对象。"""

        factory = get_session_factory()
        async with factory() as session:
            legacy_name = PurePosixPath(path).name
            match_legacy_root = "/" not in path
            path_predicates = [
                KnowledgeDocument.path == path,
                KnowledgeDocument.pending_document["path"].astext == path,
            ]
            if match_legacy_root:
                path_predicates.append(and_(
                    KnowledgeDocument.path.is_(None),
                    or_(KnowledgeDocument.file_name == legacy_name,
                        and_(KnowledgeDocument.file_name.is_(None), KnowledgeDocument.title == legacy_name)),
                ))
            row = await session.scalar(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.owner_user_id == owner_user_id, or_(*path_predicates))
                .order_by(KnowledgeDocument.updated_at.desc())
                .limit(1)
                .with_for_update()
            )
            if row is None:
                return None
            current = row.pending_document or {}
            return {
                "document_id": row.id,
                "content_sha256": current.get("content_sha256", row.content_sha256),
                "title": current.get("title", row.title),
                "file_name": current.get("file_name", row.file_name),
                "metadata": current.get("metadata", row.metadata_json),
                "sync_status": row.sync_status,
                "sync_error_code": row.sync_error_code,
                "chunking_config": current.get("chunking_config", row.chunking_config),
                "path": current.get("path", row.path),
                "chunk_count": await session.scalar(
                    select(func.count()).select_from(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == row.id, KnowledgeChunk.is_active.is_(True)
                    )
                ),
            }

    async def get_active_version(self, document_id: str, owner_user_id: str) -> dict[str, Any] | None:
        """Read confirmed source and active chunk IDs without overlaying the pending draft."""
        factory = get_session_factory()
        async with factory() as session:
            row = await session.scalar(select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id, KnowledgeDocument.owner_user_id == owner_user_id,
            ))
            if row is None or row.content is None:
                return None
            ids = list((await session.scalars(select(KnowledgeChunk.id).where(
                KnowledgeChunk.document_id == document_id, KnowledgeChunk.is_active.is_(True),
            ))).all())
            all_count = await session.scalar(select(func.count()).select_from(KnowledgeChunk).where(
                KnowledgeChunk.document_id == document_id,
            ))
            return {
                "title": row.title,
                "content_sha256": row.content_sha256,
                "metadata": row.metadata_json,
                "embedded_target_fingerprint": row.embedded_target_fingerprint,
                "active_ids": ids,
                "has_inactive": bool(all_count and all_count != len(ids)),
            }

    async def document_exists(self, document_id: str) -> bool:
        """Check identifier collisions without exposing another user's document."""
        factory = get_session_factory()
        async with factory() as session:
            return bool(await session.scalar(
                select(func.count()).select_from(KnowledgeDocument)
                .where(KnowledgeDocument.id == document_id)
            ))

    async def get_document_by_id(
        self, document_id: str, owner_user_id: str
    ) -> KnowledgeDocument | None:
        """Load a document only when it belongs to the authenticated identity."""
        factory = get_session_factory()
        async with factory() as session:
            return await session.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.id == document_id,
                    KnowledgeDocument.owner_user_id == owner_user_id,
                )
            )

    async def get_document_detail(self, document_id: str, owner_user_id: str) -> dict[str, Any] | None:
        """读取当前用户的完整文档编辑数据。"""
        factory = get_session_factory()
        async with factory() as session:
            row = await session.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.id == document_id,
                    KnowledgeDocument.owner_user_id == owner_user_id,
                )
            )
            if row is None:
                return None
            current = row.pending_document or {}
            return {
                "document_id": row.id,
                "title": current.get("title", row.title),
                "path": current.get("path", row.path),
                "content": current.get("content", row.content),
                "previous_content": row.content if current and row.content else None,
                "has_content": bool(current.get("content", row.content)),
                "has_previous_version": bool(row.content),
                "metadata": current.get("metadata", row.metadata_json) or {},
                "embedded_target_fingerprint": row.embedded_target_fingerprint,
                "chunking_config": current.get("chunking_config", row.chunking_config),
                "content_sha256": current.get("content_sha256", row.content_sha256),
                "sync_status": row.sync_status,
                "sync_error_code": row.sync_error_code,
                "chunk_count": await session.scalar(
                    select(func.count()).select_from(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == row.id, KnowledgeChunk.is_active.is_(True)
                    )
                ),
            }

    async def list_directories(self, owner_user_id: str) -> list[dict[str, Any]]:
        """列出当前用户显式创建且可保存规则的目录。"""
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.scalars(
                select(KnowledgeDirectory).where(KnowledgeDirectory.owner_user_id == owner_user_id)
                .order_by(KnowledgeDirectory.path)
            )).all()
            return [{"path": row.path, "chunking_config": row.chunking_config} for row in rows]

    async def ensure_directories(self, paths: list[str], owner_user_id: str) -> None:
        """创建路径中尚不存在的目录节点，保留已设置的规则。"""
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                existing = set((await session.scalars(
                    select(KnowledgeDirectory.path).where(KnowledgeDirectory.owner_user_id == owner_user_id)
                )).all())
                for path in paths:
                    if path and path not in existing:
                        session.add(KnowledgeDirectory(
                            id=str(uuid4()), owner_user_id=owner_user_id, path=path
                        ))
                        existing.add(path)

    async def create_directory(self, path: str, owner_user_id: str) -> None:
        """创建显式目录及所有父目录。"""
        paths = ["/".join(path.split("/")[:index]) for index in range(1, len(path.split("/")) + 1)]
        await self.ensure_directories(paths, owner_user_id)

    async def get_directory_rule(self, path: str, owner_user_id: str) -> dict[str, Any] | None:
        factory = get_session_factory()
        async with factory() as session:
            row = await session.scalar(select(KnowledgeDirectory).where(
                KnowledgeDirectory.owner_user_id == owner_user_id,
                KnowledgeDirectory.path == path,
            ))
            return row.chunking_config if row else None

    async def set_directory_rule(self, path: str, rule: dict[str, Any] | None, owner_user_id: str) -> None:
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                row = await session.scalar(select(KnowledgeDirectory).where(
                    KnowledgeDirectory.owner_user_id == owner_user_id,
                    KnowledgeDirectory.path == path,
                ).with_for_update())
                if row is None:
                    row = KnowledgeDirectory(id=str(uuid4()), owner_user_id=owner_user_id, path=path)
                    session.add(row)
                row.chunking_config = rule
                add_activity(session, owner_user_id=owner_user_id, path=path,
                             event_type="directory_rule_changed", status="success")

    async def create_empty_directory(self, path: str, owner_user_id: str) -> None:
        """Create a directory only when no document or directory already occupies the path."""
        await self.create_directory(path, owner_user_id)

    async def delete_empty_directory(self, path: str, owner_user_id: str) -> bool:
        """Delete an explicit directory only if it has no descendant entries."""
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                row = await session.scalar(select(KnowledgeDirectory).where(
                    KnowledgeDirectory.owner_user_id == owner_user_id,
                    KnowledgeDirectory.path == path,
                ).with_for_update())
                if row is None:
                    return False
                escaped_path = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                doc_count = await session.scalar(select(func.count()).select_from(KnowledgeDocument).where(
                    KnowledgeDocument.owner_user_id == owner_user_id,
                    KnowledgeDocument.path.like(f"{escaped_path}/%", escape="\\"),
                ))
                child_count = await session.scalar(select(func.count()).select_from(KnowledgeDirectory).where(
                    KnowledgeDirectory.owner_user_id == owner_user_id,
                    KnowledgeDirectory.path.like(f"{escaped_path}/%", escape="\\"),
                ))
                if doc_count or child_count:
                    raise ValueError("目录非空，请先删除或移动其中的文件和子目录。")
                await session.delete(row)
                return True

    async def list_document_details(self, owner_user_id: str) -> list[dict[str, Any]]:
        """List document edit data for controlled reindex workflows."""
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.scalars(select(KnowledgeDocument).where(
                KnowledgeDocument.owner_user_id == owner_user_id,
            ).order_by(KnowledgeDocument.path))).all()
            result = []
            for row in rows:
                current = row.pending_document or {}
                result.append({
                    "document_id": row.id,
                    "title": current.get("title", row.title),
                    "path": current.get("path", row.path or row.file_name or row.title),
                    "content": current.get("content", row.content),
                    "metadata": current.get("metadata", row.metadata_json) or {},
                    "chunking_config": current.get("chunking_config", row.chunking_config),
                    "sync_status": row.sync_status,
                    "sync_error_code": row.sync_error_code,
                })
            return result

    async def update_document_path(
        self, document_id: str, owner_user_id: str, path: str, title: str, file_name: str | None
    ) -> None:
        """Update a user-owned document virtual path after its new vectors are ready."""
        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                row = await session.scalar(select(KnowledgeDocument).where(
                    KnowledgeDocument.id == document_id,
                    KnowledgeDocument.owner_user_id == owner_user_id,
                ).with_for_update())
                if row is None:
                    raise PermissionError("DOCUMENT_NOT_OWNED")
                row.path = path
                row.title = title
                row.file_name = file_name

    async def get_effective_rule(
        self, path: str, owner_user_id: str, document_rule: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """返回最近祖先的规则；文件级覆盖优先。"""
        if document_rule:
            return document_rule
        segments = path.split("/")[:-1]
        ancestors = ["/".join(segments[:index]) for index in range(0, len(segments) + 1)]
        rules = await self.list_directories(owner_user_id)
        by_path = {item["path"]: item["chunking_config"] for item in rules}
        return next((by_path[item] for item in reversed(ancestors) if by_path.get(item)), None)

    async def get_parent_contexts(
        self, owner_user_id: str, parent_ids: list[str]
    ) -> dict[tuple[str, str], str]:
        """Load parent text only for active chunks owned by the authenticated user."""
        if not parent_ids:
            return {}
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                select(KnowledgeChunk.document_id, KnowledgeChunk.parent_id, KnowledgeChunk.parent_content)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(
                    KnowledgeDocument.owner_user_id == owner_user_id,
                    KnowledgeChunk.is_active.is_(True),
                    KnowledgeChunk.parent_id.in_(set(parent_ids)),
                    KnowledgeChunk.parent_content.is_not(None),
                )
            )).all()
            return {
                (str(document_id), str(parent_id)): str(parent_content)
                for document_id, parent_id, parent_content in rows
                if parent_id and parent_content
            }

    async def get_active_chunk_signatures(
        self, owner_user_id: str, document_ids: list[str], target_fingerprint: str
    ) -> tuple[set[str], set[tuple[str, int | None, str | None]]]:
        """Resolve confirmed vector IDs/signatures for suppressing staged Gateway records."""
        if not document_ids:
            return set(), set()
        factory = get_session_factory()
        async with factory() as session:
            rows = (await session.execute(
                select(KnowledgeChunk.id, KnowledgeChunk.document_id, KnowledgeChunk.metadata_json, KnowledgeChunk.content)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(
                    KnowledgeDocument.owner_user_id == owner_user_id,
                    KnowledgeChunk.document_id.in_(set(document_ids)),
                    KnowledgeChunk.is_active.is_(True),
                    or_(
                        KnowledgeChunk.target_fingerprint == target_fingerprint,
                        and_(
                            KnowledgeDocument.embedded_target_fingerprint.is_(None),
                            KnowledgeChunk.target_fingerprint.is_(None),
                        ),
                    ),
                )
            )).all()
            return (
                {str(record_id) for record_id, _, _, _ in rows},
                {
                    (
                        str(document_id), metadata.get("chunk_index"),
                        metadata.get("chunk_sha256") or (
                            hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None
                        ),
                    )
                    for _, document_id, metadata, content in rows
                    if isinstance(metadata, dict)
                },
            )

    async def get_chunk_ids(self, document_id: str, owner_user_id: str) -> list[str]:
        """读取文档关联的全部向量 ID（包括同步未完成时的待清理 ID）。"""

        factory = get_session_factory()
        async with factory() as session:
            return list((await session.scalars(
                select(KnowledgeChunk.id)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(
                    KnowledgeChunk.document_id == document_id,
                    KnowledgeDocument.owner_user_id == owner_user_id,
                )
            )).all())

    async def delete_local_document(self, document_id: str, owner_user_id: str) -> bool:
        """在 Gateway 向量成功删除后，删除本地文档与切片映射。"""

        factory = get_session_factory()
        async with factory() as session:
            async with session.begin():
                document = await session.scalar(
                    select(KnowledgeDocument)
                    .where(
                        KnowledgeDocument.id == document_id,
                        KnowledgeDocument.owner_user_id == owner_user_id,
                    )
                    .with_for_update()
                )
                if document is None:
                    return False
                add_activity(session, owner_user_id=owner_user_id, document_id=document_id,
                             path=(document.pending_document or {}).get("path") or document.path,
                             event_type="document_deleted", status="success")
                await session.delete(document)
            return True
