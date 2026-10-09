"""服务端调用统一身份中心；不持久化或记录身份 Cookie 和登录凭据。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any

import httpx


class IdentityCenterUnavailable(Exception):
    """身份中心不可达或返回了无法处理的响应。"""


class IdentityUnauthorized(Exception):
    """身份中心未接受当前会话。"""


class IdentityRequestRejected(Exception):
    """身份中心拒绝注册/登录请求，携带安全的机器错误码。"""

    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class IdentitySession:
    """身份中心响应中的用户与仅用于设置 HttpOnly Cookie 的短期会话值。"""

    user: dict[str, Any]
    cookie_value: str
    max_age: int


@dataclass(frozen=True)
class IdentityUser:
    """身份中心验证后的规范用户身份。"""

    id: str
    username: str
    role: str


class IdentityCenterClient:
    """调用身份中心账号 API 和 Cookie 会话验证端点。

    ``IDENTITY_CENTER_BASE_URL`` 是服务端可访问的身份中心根 URL。登录响应中的
    opaque Cookie 仅从响应传递到浏览器；后续请求只转发给 ``/api/auth/me`` 验证。
    """

    def __init__(self, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = (base_url or os.getenv("IDENTITY_CENTER_BASE_URL", "")).strip().rstrip("/")
        self.transport = transport

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise IdentityCenterUnavailable("identity center URL is not configured")
        return f"{self.base_url}/api/auth/{path.lstrip('/')}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, str] | None = None,
        cookie_value: str | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if cookie_value:
            if not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", cookie_value):
                raise IdentityUnauthorized
            headers["Cookie"] = f"identity_session={cookie_value}"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=2.0), transport=self.transport
            ) as client:
                response = await client.request(
                    method, self._url(path), json=json, headers=headers
                )
        except (httpx.HTTPError, ValueError) as error:
            raise IdentityCenterUnavailable from error
        return response

    @staticmethod
    def _safe_error(response: httpx.Response) -> IdentityRequestRejected:
        code = "IDENTITY_REQUEST_REJECTED"
        try:
            body = response.json()
            detail = body.get("detail") if isinstance(body, dict) else None
            candidate = detail.get("code") if isinstance(detail, dict) else None
            if isinstance(candidate, str) and re.fullmatch(r"[A-Z0-9_]{1,64}", candidate):
                code = candidate
        except (ValueError, TypeError):
            pass
        return IdentityRequestRejected(code, response.status_code)

    async def create_session(self, action: str, username: str, password: str) -> IdentitySession:
        """代理注册或登录，并从上游 Set-Cookie 读取 opaque Cookie 到内存。"""
        if action not in {"register", "login"}:
            raise ValueError("unsupported identity action")
        response = await self._request(
            "POST", action, json={"username": username, "password": password}
        )
        if not response.is_success:
            raise self._safe_error(response)
        try:
            payload = response.json()
            user = payload["user"]
            if not isinstance(user, dict) or not isinstance(user.get("id"), str):
                raise ValueError
        except (ValueError, KeyError, TypeError) as error:
            raise IdentityCenterUnavailable from error

        cookie = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookie.load(header)
        morsel = cookie.get("identity_session")
        if morsel is None or not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", morsel.value):
            raise IdentityCenterUnavailable
        try:
            max_age = int(morsel["max-age"] or os.getenv("AUTH_SESSION_TTL_SECONDS", "43200"))
        except ValueError:
            max_age = 43200
        return IdentitySession(user=user, cookie_value=morsel.value, max_age=max(60, max_age))

    async def current_user(self, cookie_value: str | None) -> IdentityUser:
        """用转发的身份 Cookie 调用身份中心 ``/me``，禁止信任客户端 user ID。"""
        if not cookie_value:
            raise IdentityUnauthorized
        response = await self._request("GET", "me", cookie_value=cookie_value)
        if response.status_code in {401, 403}:
            raise IdentityUnauthorized
        if not response.is_success:
            raise IdentityCenterUnavailable
        try:
            user = response.json()["user"]
            identity = IdentityUser(
                id=str(user["id"]),
                username=str(user["username"]),
                role=str(user.get("role", "user")),
            )
            if not identity.id:
                raise ValueError
            return identity
        except (ValueError, KeyError, TypeError) as error:
            raise IdentityCenterUnavailable from error

    async def logout(self, cookie_value: str | None) -> None:
        """请求身份中心撤销会话；空 Cookie 时不产生网络请求。"""
        if not cookie_value:
            return
        response = await self._request("POST", "logout", cookie_value=cookie_value)
        if not response.is_success:
            if response.status_code in {401, 403}:
                return
            raise IdentityCenterUnavailable
