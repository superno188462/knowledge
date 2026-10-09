"""模块 HTTP 路由；URL 与原版本一致。"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from app.integrations.identity.client import IdentityUser
from app.modules.auth.dependencies import require_current_user
from uuid import UUID
from .schemas import UserProfileRequest, UserMemoryCreateRequest, UserMemoryUpdateRequest, UserSessionMessagesResponse
from .service import get_user_context_client
from app.modules.credentials.errors import gateway_credential_http_error
from app.modules.credentials.service import GatewayCredentialError
from app.integrations.gateway.errors import GatewayContextError

router = APIRouter()

@router.get("/api/user-context/profile", summary="读取当前登录用户的 Gateway 用户画像")
async def get_user_profile(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    try:
        client = await get_user_context_client(user)
        return await client.get_profile(user.id)
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise gateway_credential_http_error(error) from error


@router.get(
    "/api/user-context/sessions/{session_id}/messages",
    response_model=UserSessionMessagesResponse,
    summary="读取当前用户指定会话的短期消息",
)
async def list_user_session_messages(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        client = await get_user_context_client(user)
        result = await client.list_session_messages(user.id, session_id, limit)
        return {"session_id": session_id, "messages": result["messages"]}
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise gateway_credential_http_error(error) from error


@router.delete("/api/user-context/sessions/{session_id}/messages", status_code=204, summary="清除当前用户指定会话的短期消息")
async def delete_user_session_messages(
    session_id: str,
    user: IdentityUser = Depends(require_current_user),
) -> None:
    try:
        client = await get_user_context_client(user)
        await client.delete_session_messages(user.id, session_id)
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise gateway_credential_http_error(error) from error


@router.put("/api/user-context/profile", summary="更新当前登录用户的 Gateway 用户画像")
async def update_user_profile(
    payload: UserProfileRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        client = await get_user_context_client(user)
        return await client.put_profile(user.id, payload.profile, payload.expected_version)
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise gateway_credential_http_error(error) from error


@router.get("/api/user-context/memories", summary="分页读取当前登录用户的长期记忆")
async def list_user_memories(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        client = await get_user_context_client(user)
        return await client.list_memories(user.id, page, page_size)
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise gateway_credential_http_error(error) from error


@router.post("/api/user-context/memories", status_code=201, summary="创建当前登录用户的一条长期记忆")
async def create_user_memory(
    payload: UserMemoryCreateRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        client = await get_user_context_client(user)
        return await client.create_memory(user.id, payload.content, payload.tags, payload.metadata)
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise gateway_credential_http_error(error) from error


@router.patch("/api/user-context/memories/{memory_id}", summary="按版本更新当前用户的一条长期记忆")
async def update_user_memory(
    memory_id: UUID,
    payload: UserMemoryUpdateRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        client = await get_user_context_client(user)
        return await client.update_memory(
            user.id,
            str(memory_id),
            expected_version=payload.expected_version,
            content=payload.content,
            tags=payload.tags,
            metadata=payload.metadata,
        )
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise gateway_credential_http_error(error) from error


@router.delete("/api/user-context/memories/{memory_id}", status_code=204, summary="删除当前用户的一条长期记忆")
async def delete_user_memory(
    memory_id: UUID,
    expected_version: int = Query(ge=1),
    user: IdentityUser = Depends(require_current_user),
) -> None:
    try:
        client = await get_user_context_client(user)
        await client.delete_memory(user.id, str(memory_id), expected_version)
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise gateway_credential_http_error(error) from error


