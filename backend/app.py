"""知识库数据中台 API：账号鉴权、目录元数据和 Gateway 向量生命周期。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import UUID

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from services.auth.identity_client import (
    IdentityCenterClient,
    IdentityCenterUnavailable,
    IdentityRequestRejected,
    IdentityUnauthorized,
    IdentityUser,
)
from services.rag import get_knowledge_base_service
from services.rag.knowledge_base import normalize_directory_path
from services.rag.gateway_vector_store import GatewayVectorStoreError
from services.gateway_credentials import GatewayCredentialError, GatewayCredentialService
from services.gateway_credentials.context_client import GatewayContextError
from services.rag.embedding_queue import embedding_queue
from services.rag.activity_log import list_activity, record_activity

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

configured_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:3001,http://127.0.0.1:3001,http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

app = FastAPI(title="Knowledge Data Platform API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

_APP_IDENTITY_COOKIE = "knowledge_identity_session"


class AuthCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class KnowledgeBaseInitializeRequest(BaseModel):
    description: str = Field(default="共享知识库数据中台", max_length=1000)
    embedding_model: str = Field(min_length=1, max_length=200)
    chunk_size: int = Field(default=1000, ge=100, le=6000)


class KnowledgeDocumentRequest(BaseModel):
    document_id: str | None = Field(default=None, min_length=1, max_length=200)
    directory: str = Field(default="", max_length=1000)
    name: str | None = Field(default=None, min_length=1, max_length=500)
    source_type: str | None = None
    file_name: str | None = Field(default=None, min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=200_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunking_config: dict[str, Any] | None = Field(
        default=None,
        description="文件级切片规则覆盖；null 表示继承目录规则。",
    )


class KnowledgeDirectoryRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000, description="相对于知识库根目录的目录路径。")


class KnowledgeChunkingRuleRequest(BaseModel):
    path: str = Field(max_length=1000, description="需要设置规则的目录相对路径；空字符串代表根目录。")
    rule: dict[str, Any] | None = Field(description="目录规则；null 表示移除本层规则并继承上级。")


class DocumentChunkingRuleRequest(BaseModel):
    rule: dict[str, Any] | None = Field(description="文件规则；null 表示继承目录规则。")


class KnowledgeDocumentMoveRequest(BaseModel):
    target_path: str = Field(min_length=1, max_length=1000, description="新的相对文件路径。")


class KnowledgeDocumentBatchRequest(BaseModel):
    documents: list[KnowledgeDocumentRequest] = Field(min_length=1, max_length=64)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    top_k: int = Field(default=5, ge=1, le=20)
    metadata: dict[str, Any] | None = None


class GatewayApiKeyRequest(BaseModel):
    """One-time submission of a user's Gateway project API Key."""

    api_key: SecretStr = Field(min_length=10, max_length=4000, description="仅写入；服务端验证后加密保存，不会返回明文。")


class UserProfileRequest(BaseModel):
    """Project-defined user profile JSON and optimistic concurrency version."""

    profile: dict[str, Any] = Field(description="该项目定义的简洁用户画像 JSON。")
    expected_version: int | None = Field(default=None, ge=1, description="更新已有画像时使用最近读取的版本。")


class UserMemoryCreateRequest(BaseModel):
    """A single user-owned long-term memory entry."""

    content: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UserMemoryUpdateRequest(UserMemoryCreateRequest):
    """Versioned update of a user-owned long-term memory entry."""

    expected_version: int = Field(ge=1)


class UserSessionMessagesResponse(BaseModel):
    """Short-term messages for one manually selected session owned by current user."""

    session_id: str
    messages: list[dict[str, Any]]


def get_identity_client() -> IdentityCenterClient:
    """构造服务端身份中心适配器；客户端 URL 和凭据不会进入浏览器。"""
    return IdentityCenterClient()


def get_gateway_credential_service() -> GatewayCredentialService:
    """Resolve per-user Gateway credentials from the Knowledge database."""
    return gateway_credential_service


gateway_credential_service = GatewayCredentialService()


def _gateway_credential_http_error(error: GatewayCredentialError | GatewayContextError) -> HTTPException:
    code = error.code
    status = {
        "GATEWAY_API_KEY_REQUIRED": 403,
        "GATEWAY_API_KEY_INVALID": 400,
        "GATEWAY_API_KEY_INVALID_FORMAT": 422,
        "GATEWAY_VECTOR_SERVICE_NOT_ENABLED": 403,
        "GATEWAY_CONTEXT_SERVICE_NOT_ENABLED": 403,
        "GATEWAY_CONTEXT_API_KEY_REQUIRED": 503,
        "GATEWAY_CONTEXT_ACCESS_DENIED": 403,
        "GATEWAY_CONTEXT_VERSION_CONFLICT": 409,
        "GATEWAY_CONTEXT_NOT_FOUND": 404,
        "GATEWAY_CREDENTIAL_ENCRYPTION_KEY_REQUIRED": 503,
        "GATEWAY_CREDENTIAL_ENCRYPTION_KEY_INVALID": 503,
        "GATEWAY_CREDENTIAL_DECRYPTION_FAILED": 503,
        "GATEWAY_BASE_URL_NOT_CONFIGURED": 503,
        "GATEWAY_VECTOR_CONFIGURATION_REQUIRED": 503,
        "GATEWAY_CONTEXT_TIMEOUT": 504,
        "VECTOR_GATEWAY_TIMEOUT": 504,
        "VECTOR_GATEWAY_NETWORK_ERROR": 503,
        "GATEWAY_CONTEXT_NETWORK_ERROR": 503,
    }.get(code, 502)
    messages = {
        "GATEWAY_API_KEY_REQUIRED": "请先在设置中添加 Gateway 项目 API Key。",
        "GATEWAY_API_KEY_INVALID": "Gateway API Key 无效或已撤销。",
        "GATEWAY_API_KEY_INVALID_FORMAT": "API Key 格式不正确，请检查后重试。",
        "GATEWAY_VECTOR_SERVICE_NOT_ENABLED": "该 Gateway 项目尚未开通向量数据库服务。",
        "GATEWAY_CONTEXT_SERVICE_NOT_ENABLED": "该 Gateway 项目尚未开通上下文服务。",
        "GATEWAY_CONTEXT_API_KEY_REQUIRED": "用户上下文服务尚未配置，请联系管理员配置服务端 Gateway Context Key。",
        "GATEWAY_CONTEXT_ACCESS_DENIED": "此 API Key 无权访问 Gateway 项目上下文。",
        "GATEWAY_CONTEXT_VERSION_CONFLICT": "数据已被其他请求更新，请刷新后重试。",
        "GATEWAY_CONTEXT_NOT_FOUND": "上下文记录不存在或已被删除。",
        "GATEWAY_CREDENTIAL_ENCRYPTION_KEY_REQUIRED": "服务端尚未配置 Gateway Key 加密密钥。",
        "GATEWAY_CREDENTIAL_ENCRYPTION_KEY_INVALID": "服务端 Gateway Key 加密配置无效。",
        "GATEWAY_CREDENTIAL_DECRYPTION_FAILED": "已保存的 Gateway Key 无法解密，请重新配置。",
        "GATEWAY_BASE_URL_NOT_CONFIGURED": "服务端尚未配置 Gateway 地址。",
        "GATEWAY_VECTOR_CONFIGURATION_REQUIRED": "服务端尚未配置 Gateway URL 或 collection。",
        "GATEWAY_CONTEXT_TIMEOUT": "Gateway 请求超时，请稍后重试。",
        "GATEWAY_CONTEXT_NETWORK_ERROR": "暂时无法连接 Gateway。",
        "VECTOR_GATEWAY_TIMEOUT": "Gateway 请求超时，请稍后重试。",
        "VECTOR_GATEWAY_NETWORK_ERROR": "暂时无法连接 Gateway。",
    }
    return HTTPException(status_code=status, detail={"code": code, "message": messages.get(code, "Gateway 请求失败，请检查配置后重试。")})


async def _user_knowledge_service(user: IdentityUser) -> Any:
    try:
        api_key = await get_gateway_credential_service().get_key(user.id)
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error
    return get_knowledge_base_service(api_key=api_key)


def _validate_request_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in configured_origins:
        raise HTTPException(status_code=403, detail={"code": "ORIGIN_NOT_ALLOWED"})


def _cookie_security() -> tuple[bool, str]:
    secure_setting = os.getenv("AUTH_COOKIE_SECURE")
    secure = (
        secure_setting.strip().lower() == "true"
        if secure_setting is not None
        else os.getenv("APP_ENV", "development").strip().lower() == "production"
    )
    same_site = os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    if same_site not in {"lax", "strict", "none"} or (same_site == "none" and not secure):
        raise RuntimeError("AUTH_COOKIE_SAMESITE_NONE_REQUIRES_SECURE_COOKIE")
    return secure, same_site


async def require_current_user(
    request: Request,
) -> IdentityUser:
    """只接受身份中心验证后的 user.id，不从请求参数推断用户身份。"""
    _validate_request_origin(request)
    try:
        return await get_identity_client().current_user(request.cookies.get(_APP_IDENTITY_COOKIE))
    except IdentityUnauthorized as error:
        raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED"})
    except IdentityCenterUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail={"code": "IDENTITY_CENTER_UNAVAILABLE", "message": "身份服务暂不可用，请稍后重试。"},
        ) from error


def _set_auth_cookie(response: JSONResponse, token: str, max_age: int) -> None:
    secure, same_site = _cookie_security()
    response.set_cookie(
        key=_APP_IDENTITY_COOKIE,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite=same_site,
        path="/",
    )


def _identity_error(error: IdentityRequestRejected) -> HTTPException:
    """把身份中心错误映射为有限、可展示的本项目 API 错误。"""
    messages = {
        "INVALID_CREDENTIALS": "用户名或密码错误。",
        "USERNAME_TAKEN": "这个用户名已经被注册。",
        "USERNAME_LENGTH": "用户名长度需要为 3 到 64 位。",
        "USERNAME_FORMAT": "用户名只能使用字母、数字和 . _ - @。",
        "PASSWORD_TOO_SHORT": "密码至少需要 8 位。",
        "PASSWORD_TOO_LONG": "密码不能超过 128 位。",
        "REGISTRATION_DISABLED": "当前暂不开放注册。",
        "LOGIN_RATE_LIMITED": "登录尝试过于频繁，请稍后再试。",
    }
    status_code = error.status_code if error.status_code in {401, 403, 409, 422, 429} else 502
    return HTTPException(
        status_code=status_code,
        detail={
            "code": error.code,
            "message": messages.get(error.code, "身份验证请求未能完成。"),
        },
    )


def _identity_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "IDENTITY_CENTER_UNAVAILABLE", "message": "身份服务暂不可用，请稍后重试。"},
    )


def _vector_gateway_error(error: GatewayVectorStoreError) -> HTTPException:
    status = {
        "VECTOR_STORE_NOT_CONFIGURED": 503,
        "VECTOR_DIMENSIONS_CONFIG_INVALID": 503,
        "VECTOR_COLLECTION_MODEL_MISMATCH": 409,
        "VECTOR_COLLECTION_DIMENSIONS_MISMATCH": 409,
        "VECTOR_COLLECTION_CHUNKING_MISMATCH": 409,
        "VECTOR_COLLECTION_CHUNK_SIZE_MISMATCH": 409,
        "VECTOR_GATEWAY_AUTH_FAILED": 502,
        "VECTOR_GATEWAY_SERVICE_NOT_ENABLED": 403,
        "VECTOR_GATEWAY_TIMEOUT": 504,
        "VECTOR_COLLECTION_NOT_FOUND": 404,
        "VECTOR_GATEWAY_DOCUMENT_STATE_CONFLICT": 409,
        "VECTOR_USER_QUOTA_EXCEEDED": 429,
        "VECTOR_PROJECT_QUOTA_EXCEEDED": 429,
        "VECTOR_EMBEDDING_QUOTA_EXCEEDED": 429,
        "VECTOR_QUOTA_CONFIGURATION_ERROR": 503,
        "VECTOR_RECORD_TOO_LARGE": 413,
        "VECTOR_RECORD_EMPTY": 400,
        "VECTOR_USER_QUOTA_MISSING": 403,
        "VECTOR_EMBEDDING_UPSTREAM_FAILED": 502,
        "VECTOR_EMBEDDING_INPUT_INVALID": 422,
        "VECTOR_EMBEDDING_DIMENSIONS_UNSUPPORTED": 422,
        "VECTOR_EMBEDDING_DIMENSIONS_INVALID": 502,
        "VECTOR_EMBEDDING_RESPONSE_INVALID": 502,
        "VECTOR_GATEWAY_INVALID_RESPONSE": 502,
        "VECTOR_GATEWAY_INCOMPLETE_UPSERT": 502,
        "VECTOR_GATEWAY_INVALID_REQUEST": 500,
        "VECTOR_DUPLICATE_RECORD_ID": 500,
    }.get(error.code, 502)
    messages = {
        "VECTOR_STORE_NOT_CONFIGURED": "服务端尚未配置 Gateway URL 和 collection。",
        "VECTOR_COLLECTION_MODEL_MISMATCH": "已有集合绑定了不同的 Embedding 模型。",
        "VECTOR_COLLECTION_CHUNK_SIZE_MISMATCH": "已有集合的文本长度上限与配置不一致。",
        "VECTOR_GATEWAY_AUTH_FAILED": "Gateway 项目鉴权失败。",
        "VECTOR_GATEWAY_SERVICE_NOT_ENABLED": "Gateway 项目尚未开通向量服务。",
        "VECTOR_GATEWAY_TIMEOUT": "Gateway 请求超时，请稍后重试。",
        "VECTOR_GATEWAY_NETWORK_ERROR": "暂时无法连接 Gateway。",
        "VECTOR_COLLECTION_NOT_FOUND": "集合不存在，请先初始化。",
        "VECTOR_GATEWAY_DOCUMENT_STATE_CONFLICT": "Gateway 中该切片的文档状态已变化，可能有重复嵌入请求。请刷新后再重试。",
        "VECTOR_USER_QUOTA_EXCEEDED": "当前 Gateway 用户 Embedding 额度不足。原有向量版本仍保留。",
        "VECTOR_PROJECT_QUOTA_EXCEEDED": "当前 Gateway 项目 Embedding 额度不足。原有向量版本仍保留。",
        "VECTOR_EMBEDDING_QUOTA_EXCEEDED": "Embedding 额度不足。原有向量版本仍保留。",
        "VECTOR_QUOTA_CONFIGURATION_ERROR": "Gateway Embedding 服务额度配置异常。",
        "VECTOR_RECORD_TOO_LARGE": "至少有一个切片超过 Gateway 集合的字符上限，请减小切片长度后重试。原有向量记录仍保留。",
        "VECTOR_RECORD_EMPTY": "存在空切片，请检查文档内容或切片规则。",
        "VECTOR_USER_QUOTA_MISSING": "Gateway 项目所有者尚未配置 Embedding 用户额度。",
        "VECTOR_EMBEDDING_UPSTREAM_FAILED": "Gateway 无法完成 Embedding 上游调用，请检查模型路由和模型服务状态。",
        "VECTOR_EMBEDDING_INPUT_INVALID": "Gateway 拒绝了 Embedding 输入格式。",
        "VECTOR_EMBEDDING_DIMENSIONS_UNSUPPORTED": "当前 Embedding 模型不支持集合请求的向量维度配置。",
        "VECTOR_EMBEDDING_DIMENSIONS_INVALID": "Gateway 返回的向量维度无效。",
        "VECTOR_EMBEDDING_RESPONSE_INVALID": "Gateway 返回的 Embedding 响应格式无效。",
        "VECTOR_GATEWAY_INVALID_RESPONSE": "Gateway 返回的向量响应格式无效，文档尚未标记为嵌入成功。",
        "VECTOR_GATEWAY_INCOMPLETE_UPSERT": "Gateway 未确认全部切片写入，文档尚未标记为嵌入成功。",
        "VECTOR_GATEWAY_INVALID_REQUEST": "本项目构造的切片请求无效，请检查后端日志。",
        "VECTOR_DUPLICATE_RECORD_ID": "同一批切片存在重复 ID，请检查文档切片配置。",
    }
    detail: dict[str, Any] = {
        "code": error.code,
        "message": messages.get(error.code, "向量服务请求失败。"),
    }
    if error.status_code is not None:
        detail["gateway_status"] = error.status_code
    if error.gateway_trace_id:
        detail["gateway_trace_id"] = error.gateway_trace_id
    if error.upstream_code:
        # Expose only the Gateway's stable machine code, never its response body.
        detail["gateway_code"] = error.upstream_code
        if error.code == "VECTOR_GATEWAY_ERROR":
            detail["message"] = f"Gateway 向量请求失败（错误码：{error.upstream_code}）。"
    elif error.code == "VECTOR_GATEWAY_ERROR" and error.status_code is not None:
        detail["message"] = f"Gateway 向量请求返回 HTTP {error.status_code}。"
        if error.gateway_trace_id:
            detail["message"] += f" Trace ID：{error.gateway_trace_id}。"
    return HTTPException(status_code=status, detail=detail)


@app.get("/api/settings/gateway-key", summary="读取当前用户 Gateway Key 状态")
async def gateway_key_status(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    """Return only validation state and key suffix; never return the saved secret."""
    status = await get_gateway_credential_service().access_status(user.id)
    if status.get("error_code"):
        error = GatewayCredentialError(str(status["error_code"]))
        status["message"] = _gateway_credential_http_error(error).detail["message"]
    return status


@app.put("/api/settings/gateway-key", summary="验证并保存当前用户 Gateway Key")
async def save_gateway_key(
    payload: GatewayApiKeyRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    """Validate both Gateway vector and context access before saving encrypted key data."""
    api_key = payload.api_key.get_secret_value()
    service = get_gateway_credential_service()
    try:
        access = await service.validate(api_key)
        info = await service.save_key(user.id, api_key)
        return {"configured": True, "valid": True, **info, **access}
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error


@app.delete("/api/settings/gateway-key", status_code=204, summary="删除当前用户 Gateway Key")
async def delete_gateway_key(user: IdentityUser = Depends(require_current_user)) -> None:
    """Remove this user's encrypted Gateway credential."""
    await get_gateway_credential_service().delete_for_user(user.id)


@app.get("/api/user-context/profile", summary="读取当前登录用户的 Gateway 用户画像")
async def get_user_profile(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    try:
        client = await get_gateway_credential_service().get_context_client_for_user(user.id)
        return await client.get_profile(user.id)
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise _gateway_credential_http_error(error) from error


@app.get(
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
        client = await get_gateway_credential_service().get_context_client_for_user(user.id)
        result = await client.list_session_messages(user.id, session_id, limit)
        return {"session_id": session_id, "messages": result["messages"]}
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise _gateway_credential_http_error(error) from error


@app.delete("/api/user-context/sessions/{session_id}/messages", status_code=204, summary="清除当前用户指定会话的短期消息")
async def delete_user_session_messages(
    session_id: str,
    user: IdentityUser = Depends(require_current_user),
) -> None:
    try:
        client = await get_gateway_credential_service().get_context_client_for_user(user.id)
        await client.delete_session_messages(user.id, session_id)
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise _gateway_credential_http_error(error) from error


@app.put("/api/user-context/profile", summary="更新当前登录用户的 Gateway 用户画像")
async def update_user_profile(
    payload: UserProfileRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        client = await get_gateway_credential_service().get_context_client_for_user(user.id)
        return await client.put_profile(user.id, payload.profile, payload.expected_version)
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise _gateway_credential_http_error(error) from error


@app.get("/api/user-context/memories", summary="分页读取当前登录用户的长期记忆")
async def list_user_memories(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        client = await get_gateway_credential_service().get_context_client_for_user(user.id)
        return await client.list_memories(user.id, page, page_size)
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise _gateway_credential_http_error(error) from error


@app.post("/api/user-context/memories", status_code=201, summary="创建当前登录用户的一条长期记忆")
async def create_user_memory(
    payload: UserMemoryCreateRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        client = await get_gateway_credential_service().get_context_client_for_user(user.id)
        return await client.create_memory(user.id, payload.content, payload.tags, payload.metadata)
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise _gateway_credential_http_error(error) from error


@app.patch("/api/user-context/memories/{memory_id}", summary="按版本更新当前用户的一条长期记忆")
async def update_user_memory(
    memory_id: UUID,
    payload: UserMemoryUpdateRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        client = await get_gateway_credential_service().get_context_client_for_user(user.id)
        return await client.update_memory(
            user.id,
            str(memory_id),
            expected_version=payload.expected_version,
            content=payload.content,
            tags=payload.tags,
            metadata=payload.metadata,
        )
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise _gateway_credential_http_error(error) from error


@app.delete("/api/user-context/memories/{memory_id}", status_code=204, summary="删除当前用户的一条长期记忆")
async def delete_user_memory(
    memory_id: UUID,
    expected_version: int = Query(ge=1),
    user: IdentityUser = Depends(require_current_user),
) -> None:
    try:
        client = await get_gateway_credential_service().get_context_client_for_user(user.id)
        await client.delete_memory(user.id, str(memory_id), expected_version)
    except GatewayCredentialError as error:
        raise _gateway_credential_http_error(error) from error
    except GatewayContextError as error:
        raise _gateway_credential_http_error(error) from error


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "knowledge-data-platform"}


@app.get("/api/knowledge-base")
async def knowledge_base_status(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    try:
        service = await _user_knowledge_service(user)
        return await service.status()
    except GatewayVectorStoreError as error:
        raise _vector_gateway_error(error) from error


@app.post("/api/knowledge-base/initialize")
async def initialize_knowledge_base(
    payload: KnowledgeBaseInitializeRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await _user_knowledge_service(user)
        return await service.initialize(
            description=payload.description,
            model=payload.embedding_model,
            chunk_size=payload.chunk_size,
        )
    except GatewayVectorStoreError as error:
        raise _vector_gateway_error(error) from error


@app.get("/api/knowledge-base/documents")
async def list_knowledge_documents(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    service = await _user_knowledge_service(user)
    return {"documents": await service.list_documents(owner_user_id=user.id)}


@app.get("/api/knowledge-base/embedding-jobs", summary="读取当前用户最近的嵌入队列任务")
async def list_embedding_jobs(
    limit: int = Query(default=50, ge=1, le=100),
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    return {"jobs": await embedding_queue.list_for_user(user.id, limit)}


@app.get("/api/knowledge-base/activity-logs", summary="分页读取当前用户的文档与嵌入日志")
async def knowledge_activity_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    document_id: str | None = Query(default=None, max_length=200),
    event_type: str | None = Query(default=None, max_length=48),
    path_query: str | None = Query(default=None, max_length=200),
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, object]:
    return await list_activity(user.id, page=page, page_size=page_size,
                               document_id=document_id, event_type=event_type,
                               path_query=path_query)


@app.get("/api/knowledge-base/embedding-jobs/{job_id}", summary="读取当前用户的嵌入任务状态")
async def get_embedding_job(
    job_id: str, user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    job = await embedding_queue.get_for_user(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_JOB_NOT_FOUND"})
    return job


@app.get("/api/knowledge-base/tree", summary="读取知识库文件树")
async def knowledge_base_tree(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    service = await _user_knowledge_service(user)
    return await service.get_tree(owner_user_id=user.id)


@app.post("/api/knowledge-base/directories", summary="创建知识库目录")
async def create_knowledge_directory(
    payload: KnowledgeDirectoryRequest, user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    try:
        service = await _user_knowledge_service(user)
        result = await service.create_directory(payload.path, owner_user_id=user.id)
        await record_activity(owner_user_id=user.id, path=normalize_directory_path(payload.path),
                              event_type="directory_created", status="success")
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "INVALID_KNOWLEDGE_DIRECTORY", "message": str(error)}) from error


@app.delete("/api/knowledge-base/directories", summary="删除空知识库目录")
async def delete_knowledge_directory(
    path: str = Query(min_length=1, max_length=1000), user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    try:
        service = await _user_knowledge_service(user)
        deleted = await service.delete_directory(path, owner_user_id=user.id)
        if deleted:
            await record_activity(owner_user_id=user.id, path=normalize_directory_path(path),
                                  event_type="directory_deleted", status="success")
        return {"deleted": deleted}
    except ValueError as error:
        raise HTTPException(status_code=409, detail={"code": "KNOWLEDGE_DIRECTORY_NOT_EMPTY", "message": str(error)}) from error


@app.put("/api/knowledge-base/directories/rule", summary="设置目录切片规则并将受影响文档加入嵌入队列")
async def set_knowledge_directory_rule(
    payload: KnowledgeChunkingRuleRequest, user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    try:
        service = await _user_knowledge_service(user)
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
        raise _vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "INVALID_CHUNKING_RULE", "message": str(error)}) from error


@app.get("/api/knowledge-base/documents/{document_id}", summary="读取文档原文和切片规则")
async def get_knowledge_document(
    document_id: str, user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    service = await _user_knowledge_service(user)
    document = await service.get_document(document_id, owner_user_id=user.id)
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"})
    return document


@app.put("/api/knowledge-base/documents/{document_id}/chunking-rule", summary="设置文件切片规则并加入嵌入队列")
async def set_knowledge_document_rule(
    document_id: str, payload: DocumentChunkingRuleRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await _user_knowledge_service(user)
        await service.set_document_rule(document_id, payload.rule, owner_user_id=user.id)
        detail = await service.repository.get_document_detail(document_id, user.id)
        await record_activity(owner_user_id=user.id, document_id=document_id,
                              path=detail["path"] if detail else None,
                              event_type="document_rule_changed", status="success")
        return await embedding_queue.enqueue(document_id, user.id)
    except GatewayVectorStoreError as error:
        raise _vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "INVALID_CHUNKING_RULE", "message": str(error)}) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"}) from error


@app.put("/api/knowledge-base/documents/{document_id}/move", summary="移动或重命名知识库文档")
async def move_knowledge_document(
    document_id: str, payload: KnowledgeDocumentMoveRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await _user_knowledge_service(user)
        await service.move_document(document_id, payload.target_path, owner_user_id=user.id)
        detail = await service.repository.get_document_detail(document_id, user.id)
        await record_activity(owner_user_id=user.id, document_id=document_id,
                              path=detail["path"] if detail else None,
                              event_type="document_moved", status="success")
        return await embedding_queue.enqueue(document_id, user.id)
    except GatewayVectorStoreError as error:
        raise _vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DOCUMENT_PATH", "message": str(error)}) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"}) from error


@app.post("/api/knowledge-base/documents/{document_id}/reindex", status_code=202, summary="将文档加入嵌入队列")
async def reindex_knowledge_document(
    document_id: str, user: IdentityUser = Depends(require_current_user)
) -> dict[str, Any]:
    try:
        await _user_knowledge_service(user)
        return await embedding_queue.enqueue(document_id, user.id)
    except GatewayVectorStoreError as error:
        raise _vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "DOCUMENT_REQUIRES_UPLOAD", "message": str(error)}) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"}) from error


@app.post("/api/knowledge-base/documents/batch", status_code=202)
async def upsert_knowledge_documents(
    payload: KnowledgeDocumentBatchRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await _user_knowledge_service(user)
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
        raise _vector_gateway_error(error) from error
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_KNOWLEDGE_DOCUMENT", "message": str(error)},
        ) from error
    except PermissionError as error:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND"}) from error


@app.post("/api/knowledge-base/documents/drafts")
async def save_knowledge_document_drafts(
    payload: KnowledgeDocumentBatchRequest,
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    """Save source documents locally without making Gateway embedding requests."""
    try:
        service = await _user_knowledge_service(user)
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


@app.delete("/api/knowledge-base/documents")
async def delete_knowledge_document(
    document_id: str = Query(min_length=1, max_length=200),
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    try:
        service = await _user_knowledge_service(user)
        return await service.delete_document(
            document_id, owner_user_id=user.id
        )
    except GatewayVectorStoreError as error:
        raise _vector_gateway_error(error) from error


@app.post("/api/knowledge-base/search")
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
        service = await _user_knowledge_service(user)
        results = await service.search(
            payload.query,
            owner_user_id=user.id,
            top_k=payload.top_k,
            metadata=metadata,
        )
        return {"results": results}
    except GatewayVectorStoreError as error:
        raise _vector_gateway_error(error) from error


@app.post("/api/auth/register", status_code=201)
async def auth_register(
    credentials: AuthCredentials,
    request: Request,
) -> JSONResponse:
    """代理身份中心注册；密码只在本次服务端请求中转发，不落本地数据库或日志。"""
    _validate_request_origin(request)
    try:
        session = await get_identity_client().create_session(
            "register", credentials.username, credentials.password
        )
    except IdentityRequestRejected as error:
        raise _identity_error(error) from error
    except IdentityCenterUnavailable as error:
        raise _identity_unavailable() from error
    response = JSONResponse({"user": session.user}, status_code=201)
    _set_auth_cookie(response, session.cookie_value, session.max_age)
    return response


@app.post("/api/auth/login")
async def auth_login(
    credentials: AuthCredentials,
    request: Request,
) -> JSONResponse:
    """代理身份中心登录；不创建本地账号或会话。"""
    _validate_request_origin(request)
    try:
        session = await get_identity_client().create_session(
            "login", credentials.username, credentials.password
        )
    except IdentityRequestRejected as error:
        raise _identity_error(error) from error
    except IdentityCenterUnavailable as error:
        raise _identity_unavailable() from error
    response = JSONResponse({"user": session.user})
    _set_auth_cookie(response, session.cookie_value, session.max_age)
    return response


@app.get("/api/auth/me")
async def auth_me(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    return {"user": {"id": user.id, "username": user.username, "role": user.role, "permissions": []}}


@app.post("/api/auth/logout")
async def auth_logout(request: Request) -> JSONResponse:
    """请求身份中心撤销 opaque 会话，并清除 Knowledge 域下的 HttpOnly Cookie。"""
    _validate_request_origin(request)
    identity_error = False
    try:
        await get_identity_client().logout(request.cookies.get(_APP_IDENTITY_COOKIE))
    except IdentityUnauthorized:
        # A malformed or expired local relay cookie is already unauthenticated upstream.
        pass
    except IdentityCenterUnavailable:
        identity_error = True
    secure, same_site = _cookie_security()
    response = JSONResponse(
        {"logged_out": not identity_error}
        if not identity_error
        else {"detail": {"code": "IDENTITY_CENTER_UNAVAILABLE", "message": "身份服务暂不可用，请稍后重试。"}},
        status_code=503 if identity_error else 200,
    )
    response.delete_cookie(
        _APP_IDENTITY_COOKIE, path="/", httponly=True, secure=secure, samesite=same_site
    )
    return response
