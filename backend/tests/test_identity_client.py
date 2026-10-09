"""Identity Center HTTP adapter contract tests using an in-memory transport."""

import asyncio

import httpx
import pytest

from services.auth.identity_client import (
    IdentityCenterClient,
    IdentityCenterUnavailable,
    IdentityRequestRejected,
    IdentityUnauthorized,
)


SESSION_COOKIE = "a" * 48


def run(awaitable):
    return asyncio.run(awaitable)


def test_login_forwards_credentials_once_and_extracts_httponly_cookie():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/auth/login"
        assert request.read() == b'{"username":"demo","password":"a-long-password"}'
        return httpx.Response(
            200,
            json={"user": {"id": "canonical-user-id", "username": "demo"}},
            headers={"set-cookie": f"identity_session={SESSION_COOKIE}; HttpOnly; Path=/; Max-Age=1800; SameSite=lax"},
        )

    client = IdentityCenterClient("https://identity.example.test", transport=httpx.MockTransport(handler))
    session = run(client.create_session("login", "demo", "a-long-password"))

    assert session.user["id"] == "canonical-user-id"
    assert session.cookie_value == SESSION_COOKIE
    assert session.max_age == 1800


def test_current_user_is_resolved_only_by_identity_center_cookie():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/auth/me"
        assert request.headers["cookie"] == f"identity_session={SESSION_COOKIE}"
        return httpx.Response(
            200,
            json={"user": {"id": "identity-user-42", "username": "alice", "role": "user"}},
        )

    client = IdentityCenterClient("http://identity.test", transport=httpx.MockTransport(handler))
    user = run(client.current_user(SESSION_COOKIE))
    assert user.id == "identity-user-42"
    assert user.username == "alice"


def test_identity_rejections_are_sanitized_and_session_failures_are_distinct():
    rejection = IdentityCenterClient(
        "http://identity.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(409, json={"detail": {"code": "USERNAME_TAKEN"}})
        ),
    )
    with pytest.raises(IdentityRequestRejected) as error:
        run(rejection.create_session("register", "alice", "long-password"))
    assert error.value.code == "USERNAME_TAKEN"

    unauthorized = IdentityCenterClient(
        "http://identity.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "AUTH_REQUIRED"})),
    )
    with pytest.raises(IdentityUnauthorized):
        run(unauthorized.current_user(SESSION_COOKIE))


def test_identity_network_failure_is_not_misreported_as_no_session():
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = IdentityCenterClient("http://identity.test", transport=httpx.MockTransport(fail))
    with pytest.raises(IdentityCenterUnavailable):
        run(client.current_user(SESSION_COOKIE))
