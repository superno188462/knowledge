"""服务端验证统一身份，提供 Cookie 与 Origin 边界。"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from app import dependencies
from app.settings import get_settings
from app.integrations.identity.client import IdentityUser, IdentityUnauthorized, IdentityCenterUnavailable, IdentityRequestRejected

configured_origins = list(get_settings().cors_origins)
_APP_IDENTITY_COOKIE = "knowledge_identity_session"

def _validate_request_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in configured_origins:
        raise HTTPException(status_code=403, detail={"code": "ORIGIN_NOT_ALLOWED"})


def _cookie_security() -> tuple[bool, str]:
    settings = get_settings()
    secure = settings.cookie_secure
    same_site = settings.cookie_samesite
    if same_site not in {"lax", "strict", "none"} or (same_site == "none" and not secure):
        raise RuntimeError("AUTH_COOKIE_SAMESITE_NONE_REQUIRES_SECURE_COOKIE")
    return secure, same_site


async def require_current_user(
    request: Request,
) -> IdentityUser:
    """只接受身份中心验证后的 user.id，不从请求参数推断用户身份。"""
    _validate_request_origin(request)
    try:
        return await dependencies.get_identity_client().current_user(request.cookies.get(_APP_IDENTITY_COOKIE))
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


