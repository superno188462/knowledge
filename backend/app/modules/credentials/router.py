"""模块 HTTP 路由；URL 与原版本一致。"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from app.integrations.identity.client import IdentityUser
from app.modules.auth.dependencies import require_current_user
from app import dependencies
from .schemas import GatewayApiKeyRequest, GatewayCollectionRequest, GatewayCollectionCreateRequest
from .service import GatewayCredentialError
from .errors import gateway_credential_http_error
from app.modules.embedding_jobs.worker import embedding_queue
from app.integrations.gateway.errors import GatewayVectorStoreError
from app.modules.knowledge.errors import vector_gateway_error

router = APIRouter()


async def _require_idle(user_id: str) -> None:
    if await embedding_queue.has_active_jobs(user_id):
        raise HTTPException(status_code=409, detail={
            "code": "EMBEDDING_JOB_ACTIVE",
            "message": "仍有文件排队或嵌入中，请完成后再切换集合或更换 Key。",
        })

@router.get("/api/settings/gateway-key", summary="读取当前用户 Gateway Key 状态")
async def gateway_key_status(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    """Return only validation state and key suffix; never return the saved secret."""
    status = await dependencies.get_gateway_credential_service().access_status(user.id)
    if status.get("error_code"):
        error = GatewayCredentialError(str(status["error_code"]))
        status["message"] = gateway_credential_http_error(error).detail["message"]
    return status


@router.put("/api/settings/gateway-key", summary="验证并保存当前用户 Gateway Key")
async def save_gateway_key(
    payload: GatewayApiKeyRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    """Validate both Gateway vector and context access before saving encrypted key data."""
    api_key = payload.api_key.get_secret_value()
    service = dependencies.get_gateway_credential_service()
    try:
        await _require_idle(user.id)
        access = await service.validate(api_key)
        info = await service.save_key(user.id, api_key)
        return {"configured": True, "valid": True, **info, **access}
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error


@router.delete("/api/settings/gateway-key", status_code=204, summary="删除当前用户 Gateway Key")
async def delete_gateway_key(user: IdentityUser = Depends(require_current_user)) -> None:
    """Remove this user's encrypted Gateway credential."""
    await _require_idle(user.id)
    await dependencies.get_gateway_credential_service().delete_for_user(user.id)


@router.put("/api/settings/gateway-collection", summary="选择当前用户的 Gateway 集合")
async def select_gateway_collection(payload: GatewayCollectionRequest, user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    await _require_idle(user.id)
    try:
        return await dependencies.get_gateway_credential_service().select_collection(user.id, payload.collection)
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error


@router.post("/api/settings/gateway-collections", summary="创建并选择 Gateway 集合")
async def create_gateway_collection(payload: GatewayCollectionCreateRequest, user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    await _require_idle(user.id)
    service = dependencies.get_gateway_credential_service()
    try:
        store = service.vector_store(await service.get_key(user.id), payload.collection)
        await store.create_collection(payload.embedding_model, description=payload.description, chunk_size=payload.chunk_size)
        return await service.select_collection(user.id, payload.collection)
    except GatewayCredentialError as error:
        raise gateway_credential_http_error(error) from error
    except GatewayVectorStoreError as error:
        raise vector_gateway_error(error) from error


