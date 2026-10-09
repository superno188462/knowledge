"""模块 HTTP 路由；URL 与原版本一致。"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from app.integrations.identity.client import IdentityUser
from app.modules.auth.dependencies import require_current_user
from app import dependencies
from .schemas import AuthCredentials
from .dependencies import _validate_request_origin, _set_auth_cookie, _identity_error, _identity_unavailable, _cookie_security, _APP_IDENTITY_COOKIE
from app.integrations.identity.client import IdentityRequestRejected, IdentityCenterUnavailable, IdentityUnauthorized

router = APIRouter()

@router.post("/api/auth/register", status_code=201)
async def auth_register(
    credentials: AuthCredentials,
    request: Request,
) -> JSONResponse:
    """代理身份中心注册；密码只在本次服务端请求中转发，不落本地数据库或日志。"""
    _validate_request_origin(request)
    try:
        session = await dependencies.get_identity_client().create_session(
            "register", credentials.username, credentials.password
        )
    except IdentityRequestRejected as error:
        raise _identity_error(error) from error
    except IdentityCenterUnavailable as error:
        raise _identity_unavailable() from error
    response = JSONResponse({"user": session.user}, status_code=201)
    _set_auth_cookie(response, session.cookie_value, session.max_age)
    return response


@router.post("/api/auth/login")
async def auth_login(
    credentials: AuthCredentials,
    request: Request,
) -> JSONResponse:
    """代理身份中心登录；不创建本地账号或会话。"""
    _validate_request_origin(request)
    try:
        session = await dependencies.get_identity_client().create_session(
            "login", credentials.username, credentials.password
        )
    except IdentityRequestRejected as error:
        raise _identity_error(error) from error
    except IdentityCenterUnavailable as error:
        raise _identity_unavailable() from error
    response = JSONResponse({"user": session.user})
    _set_auth_cookie(response, session.cookie_value, session.max_age)
    return response


@router.get("/api/auth/me")
async def auth_me(user: IdentityUser = Depends(require_current_user)) -> dict[str, Any]:
    return {"user": {"id": user.id, "username": user.username, "role": user.role, "permissions": []}}


@router.post("/api/auth/logout")
async def auth_logout(request: Request) -> JSONResponse:
    """请求身份中心撤销 opaque 会话，并清除 Knowledge 域下的 HttpOnly Cookie。"""
    _validate_request_origin(request)
    identity_error = False
    try:
        await dependencies.get_identity_client().logout(request.cookies.get(_APP_IDENTITY_COOKIE))
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


