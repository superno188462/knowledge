"""Small HTTP adapter for the Gateway project-context runtime API."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import httpx


class GatewayContextError(RuntimeError):
    """Safe Gateway context error code and optional upstream status."""

    def __init__(self, code: str, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class GatewayContextClient:
    """Calls context routes without exposing the user's key to the browser."""

    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        base_url = os.getenv("GATEWAY_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            raise GatewayContextError("GATEWAY_BASE_URL_NOT_CONFIGURED")
        self.base_url = f"{base_url}/context"
        self.api_key = api_key
        self.transport = transport

    @staticmethod
    def _user_path(user_id: str) -> str:
        return f"/users/{quote(user_id, safe='')}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, int] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    params=params,
                    json=json,
                )
        except httpx.TimeoutException as error:
            raise GatewayContextError("GATEWAY_CONTEXT_TIMEOUT") from error
        except httpx.HTTPError as error:
            raise GatewayContextError("GATEWAY_CONTEXT_NETWORK_ERROR") from error

        if response.status_code >= 400:
            upstream_code = None
            try:
                body = response.json()
                detail = body.get("detail") if isinstance(body, dict) else None
                upstream_code = detail.get("code") if isinstance(detail, dict) else None
            except ValueError:
                pass
            if response.status_code == 401:
                code = "GATEWAY_API_KEY_INVALID"
            elif response.status_code == 403:
                code = (
                    "GATEWAY_CONTEXT_SERVICE_NOT_ENABLED"
                    if upstream_code == "service_not_enabled"
                    else "GATEWAY_CONTEXT_ACCESS_DENIED"
                )
            elif response.status_code == 409:
                code = "GATEWAY_CONTEXT_VERSION_CONFLICT"
            elif response.status_code == 404:
                code = "GATEWAY_CONTEXT_NOT_FOUND"
            else:
                code = "GATEWAY_CONTEXT_ERROR"
            raise GatewayContextError(code, response.status_code)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise GatewayContextError("GATEWAY_CONTEXT_INVALID_RESPONSE", response.status_code) from error

    async def validate_user_access(self, user_id: str) -> None:
        """Read this user's profile to verify the key can access project context."""
        result = await self._request("GET", f"{self._user_path(user_id)}/profile")
        if not isinstance(result, dict) or not isinstance(result.get("profile"), dict):
            raise GatewayContextError("GATEWAY_CONTEXT_INVALID_RESPONSE")

    async def list_session_messages(
        self, user_id: str, session_id: str, limit: int
    ) -> dict[str, Any]:
        result = await self._request(
            "GET",
            f"{self._user_path(user_id)}/sessions/{quote(session_id, safe='')}/messages",
            params={"limit": limit},
        )
        if not isinstance(result, dict) or not isinstance(result.get("messages"), list):
            raise GatewayContextError("GATEWAY_CONTEXT_INVALID_RESPONSE")
        return result

    async def delete_session_messages(self, user_id: str, session_id: str) -> None:
        await self._request(
            "DELETE",
            f"{self._user_path(user_id)}/sessions/{quote(session_id, safe='')}/messages",
        )

    async def get_profile(self, user_id: str) -> dict[str, Any]:
        result = await self._request("GET", f"{self._user_path(user_id)}/profile")
        if not isinstance(result, dict) or not isinstance(result.get("profile"), dict):
            raise GatewayContextError("GATEWAY_CONTEXT_INVALID_RESPONSE")
        return {"profile": result["profile"], "version": result.get("version")}

    async def put_profile(
        self, user_id: str, profile: dict[str, Any], expected_version: int | None
    ) -> dict[str, Any]:
        result = await self._request(
            "PUT",
            f"{self._user_path(user_id)}/profile",
            json={"profile": profile, "expected_version": expected_version},
        )
        if not isinstance(result, dict):
            raise GatewayContextError("GATEWAY_CONTEXT_INVALID_RESPONSE")
        return {"profile": result.get("profile", profile), "version": result.get("version")}

    async def list_memories(self, user_id: str, page: int, page_size: int) -> dict[str, Any]:
        result = await self._request(
            "GET",
            f"{self._user_path(user_id)}/memories",
            params={"page": page, "page_size": page_size},
        )
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise GatewayContextError("GATEWAY_CONTEXT_INVALID_RESPONSE")
        return result

    async def create_memory(
        self, user_id: str, content: str, tags: list[str], metadata: dict[str, Any]
    ) -> dict[str, Any]:
        result = await self._request(
            "POST",
            f"{self._user_path(user_id)}/memories",
            json={"content": content, "tags": tags, "metadata": metadata},
        )
        if not isinstance(result, dict):
            raise GatewayContextError("GATEWAY_CONTEXT_INVALID_RESPONSE")
        return result

    async def update_memory(
        self,
        user_id: str,
        memory_id: str,
        *,
        expected_version: int,
        content: str,
        tags: list[str],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self._request(
            "PATCH",
            f"{self._user_path(user_id)}/memories/{quote(memory_id, safe='')}",
            json={
                "expected_version": expected_version,
                "content": content,
                "tags": tags,
                "metadata": metadata,
            },
        )
        if not isinstance(result, dict):
            raise GatewayContextError("GATEWAY_CONTEXT_INVALID_RESPONSE")
        return result

    async def delete_memory(self, user_id: str, memory_id: str, expected_version: int) -> None:
        await self._request(
            "DELETE",
            f"{self._user_path(user_id)}/memories/{quote(memory_id, safe='')}",
            params={"expected_version": expected_version},
        )
