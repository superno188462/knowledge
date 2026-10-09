"""模块 HTTP 路由；URL 与原版本一致。"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from app.integrations.identity.client import IdentityUser
from app.modules.auth.dependencies import require_current_user
from app import dependencies
from .schemas import (KnowledgeBaseInitializeRequest, KnowledgeDocumentBatchRequest, KnowledgeSearchRequest, KnowledgeDirectoryRequest, KnowledgeChunkingRuleRequest, DocumentChunkingRuleRequest, KnowledgeDocumentMoveRequest)
from .service import normalize_directory_path
from .errors import vector_gateway_error
from app.integrations.gateway.errors import GatewayVectorStoreError
from app.modules.embedding_jobs.worker import embedding_queue
from app.modules.activity_logs.service import record_activity

router = APIRouter()

@router.get("/api/knowledge-base")
async def knowledge_base_status(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    try:
        service = await dependencies.user_knowledge_service(user)
        return await service.status()
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error


@router.post("/api/knowledge-base/initialize")
async def initialize_knowledge_base(
    payload: KnowledgeBaseInitializeRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await dependencies.user_knowledge_service(user)
        return await service.initialize(
            description=payload.description,
            model=payload.embedding_model,
            chunk_size=payload.chunk_size,
        )
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error


@router.get("/api/knowledge-base/documents")
async def list_knowledge_documents(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    service = await dependencies.user_knowledge_service(user)
    return {"documents": await service.list_documents(owner_user_id=user.id)}


@router.get("/api/knowledge-base/tree", summary="读取知识库文件树")
async def knowledge_base_tree(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    service = await dependencies.user_knowledge_service(user)
    return await service.get_tree(owner_user_id=user.id)


@router.post("/api/knowledge-base/directories", summary="创建知识库目录")
async def create_knowledge_directory(
    payload: KnowledgeDirectoryRequest, user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    try:
        service = await dependencies.user_knowledge_service(user)
        result = await service.create_directory(payload.path, owner_user_id=user.id)
        await record_activity(owner_user_id=user.id, path=normalize_directory_path(payload.path),
                              event_type="directory_created", status="success")
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "INVALID_KNOWLEDGE_DIRECTORY", "message": str(error)}) from error


@router.delete("/api/knowledge-base/directories", summary="删除空知识库目录")
async def delete_knowledge_directory(
    path: str = Query(min_length=1, max_length=1000), user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    try:
        service = await dependencies.user_knowledge_service(user)
        deleted = await service.delete_directory(path, owner_user_id=user.id)
        if deleted:
            await record_activity(owner_user_id=user.id, path=normalize_directory_path(path),
                                  event_type="directory_deleted", status="success")
        return {"deleted": deleted}
    except ValueError as error:
        raise HTTPException(status_code=409, detail={"code": "KNOWLEDGE_DIRECTORY_NOT_EMPTY", "message": str(error)}) from error


@router.put("/api/knowledge-base/directories/rule", summary="设置目录切片规则并将受影响文档加入嵌入队列")
async def set_knowledge_directory_rule(
    payload: KnowledgeChunkingRuleRequest, user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    try:
        service = await dependencies.user_knowledge_service(user)
        docs = await service.repository.list_document_details(user.id)
        normalized_path = normalize_directory_path(payload.path)
        affected_ids = [
            doc["document_id"] for doc in docs
            if not normalized_path or str(doc["path"]).startswith(normalized_path + "/")
        ]
        if await embedding_queue.active_for_documents(user.id, affected_ids):
            raise HTTPException(status_code=409, detail={
                "code": "EMBEDDING_JOB_ACTIVE",
                "message": "此目录仍有文件排队或嵌入中，请完成后再修改目录规则。",
            })
        result = await service.set_directory_rule(payload.path, payload.rule, owner_user_id=user.id)
        for item in result["documents"]:
            if item["status"] == "pending":
                item["job"] = await embedding_queue.enqueue(item["document_id"], user.id)
                item["status"] = "queued"
        return result
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "INVALID_CHUNKING_RULE", "message": str(error)}) from error


@router.get("/api/knowledge-base/documents/{document_id}", summary="读取文档原文和切片规则")
async def get_knowledge_document(
    document_id: str, user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    service = await dependencies.user_knowledge_service(user)
    document = await service.get_document(document_id, owner_user_id=user.id)
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"})
    return document


@router.put("/api/knowledge-base/documents/{document_id}/chunking-rule", summary="设置文件切片规则并加入嵌入队列")
async def set_knowledge_document_rule(
    document_id: str, payload: DocumentChunkingRuleRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await dependencies.user_knowledge_service(user)
        await service.set_document_rule(document_id, payload.rule, owner_user_id=user.id)
        detail = await service.repository.get_document_detail(document_id, user.id)
        await record_activity(owner_user_id=user.id, document_id=document_id,
                              path=detail["path"] if detail else None,
                              event_type="document_rule_changed", status="success")
        return await embedding_queue.enqueue(document_id, user.id)
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "INVALID_CHUNKING_RULE", "message": str(error)}) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"}) from error


@router.put("/api/knowledge-base/documents/{document_id}/move", summary="移动或重命名知识库文档")
async def move_knowledge_document(
    document_id: str, payload: KnowledgeDocumentMoveRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await dependencies.user_knowledge_service(user)
        await service.move_document(document_id, payload.target_path, owner_user_id=user.id)
        detail = await service.repository.get_document_detail(document_id, user.id)
        await record_activity(owner_user_id=user.id, document_id=document_id,
                              path=detail["path"] if detail else None,
                              event_type="document_moved", status="success")
        return await embedding_queue.enqueue(document_id, user.id)
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DOCUMENT_PATH", "message": str(error)}) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"}) from error


@router.post("/api/knowledge-base/documents/{document_id}/reindex", status_code=202, summary="将文档加入嵌入队列")
async def reindex_knowledge_document(
    document_id: str, user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    try:
        await dependencies.user_knowledge_service(user)
        return await embedding_queue.enqueue(document_id, user.id)
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "DOCUMENT_REQUIRES_UPLOAD", "message": str(error)}) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"}) from error


@router.post("/api/knowledge-base/documents/batch", status_code=202)
async def upsert_knowledge_documents(
    payload: KnowledgeDocumentBatchRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await dependencies.user_knowledge_service(user)
        documents = [item.model_dump(exclude_unset=True) for item in payload.documents]
        results = await service.save_document_drafts(
            documents, owner_user_id=user.id
        )
        return {
            "documents": [
                {"document_id": item["id"], "job": await embedding_queue.enqueue(item["id"], user.id)}
                for item in results
            ]
        }
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_KNOWLEDGE_DOCUMENT", "message": str(error)},
        ) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"}) from error


@router.post("/api/knowledge-base/documents/drafts")
async def save_knowledge_document_drafts(
    payload: KnowledgeDocumentBatchRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    """Save source documents locally without making Gateway embedding requests."""
    try:
        service = await dependencies.user_knowledge_service(user)
        documents = [item.model_dump(exclude_unset=True) for item in payload.documents]
        results = await service.save_document_drafts(documents, owner_user_id=user.id)
        return {
            "documents": [
                {"document_id": item["id"], "result": item["result"]} for item in results
            ]
        }
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_KNOWLEDGE_DOCUMENT", "message": str(error)},
        ) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"}) from error


@router.delete("/api/knowledge-base/documents")
async def delete_knowledge_document(
    document_id: str = Query(min_length=1, max_length=200),
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await dependencies.user_knowledge_service(user)
        return await service.delete_document(
            document_id, owner_user_id=user.id
        )
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error


@router.post("/api/knowledge-base/search")
async def search_knowledge_base(
    payload: KnowledgeSearchRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    metadata = dict(payload.metadata or {})
    # These values come from the verified Identity Center session, never the request body.
    metadata.update({
        "app_id": "knowledge-platform",
        "record_type": "knowledge_chunk",
        "owner_user_id": user.id,
    })
    try:
        service = await dependencies.user_knowledge_service(user)
        results = await service.search(
            payload.query,
            owner_user_id=user.id,
            top_k=payload.top_k,
            metadata=metadata,
        )
        return {"results": results}
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error


