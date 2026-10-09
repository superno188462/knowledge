"""BFF auth and canonical-user isolation tests; no database or live identity service."""

from fastapi.testclient import TestClient
import pytest

import app as knowledge_app
from services.auth.identity_client import (
    IdentityCenterUnavailable,
    IdentitySession,
    IdentityUser,
    IdentityUnauthorized,
)


TOKEN_A = "a" * 48
TOKEN_B = "b" * 48


@pytest.fixture(autouse=True)
def allow_test_origins(monkeypatch):
    monkeypatch.setattr(
        knowledge_app,
        "configured_origins",
        ["http://localhost:3001", "http://127.0.0.1:3001"],
    )


class FakeIdentityClient:
    async def create_session(self, action, username, password):
        assert password == "password-value-is-forwarded-only"
        return IdentitySession(
            user={"id": "canonical-a", "username": username, "role": "user"},
            cookie_value=TOKEN_A,
            max_age=3600,
        )

    async def current_user(self, cookie_value):
        if cookie_value == TOKEN_A:
            return IdentityUser(id="canonical-a", username="alice", role="user")
        if cookie_value == TOKEN_B:
            return IdentityUser(id="canonical-b", username="bob", role="user")
        raise IdentityUnauthorized

    async def logout(self, cookie_value):
        if cookie_value == TOKEN_A:
            raise IdentityCenterUnavailable


class FakeKnowledgeService:
    async def list_documents(self, *, owner_user_id):
        return [{"owner_user_id": owner_user_id}]


def test_register_login_and_me_use_identity_center_cookie(monkeypatch):
    monkeypatch.setattr(knowledge_app, "get_identity_client", FakeIdentityClient)
    client = TestClient(knowledge_app.app)
    payload = {"username": "alice", "password": "password-value-is-forwarded-only"}

    registered = client.post("/api/auth/register", json=payload, headers={"Origin": "http://localhost:3001"})
    assert registered.status_code == 201
    assert registered.json()["user"]["id"] == "canonical-a"
    assert "httponly" in registered.headers["set-cookie"].lower()
    assert "samesite=lax" in registered.headers["set-cookie"].lower()
    assert registered.cookies.get("knowledge_identity_session") == TOKEN_A

    logged_in = client.post("/api/auth/login", json=payload, headers={"Origin": "http://localhost:3001"})
    assert logged_in.status_code == 200
    assert logged_in.json()["user"]["id"] == "canonical-a"

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == "canonical-a"


def test_document_listing_uses_verified_identity_not_submitted_user_id(monkeypatch):
    monkeypatch.setattr(knowledge_app, "get_identity_client", FakeIdentityClient)
    async def fake_user_knowledge_service(user):
        return FakeKnowledgeService()

    monkeypatch.setattr(knowledge_app, "_user_knowledge_service", fake_user_knowledge_service)
    client = TestClient(knowledge_app.app)
    client.cookies.set("knowledge_identity_session", TOKEN_A)

    response = client.get("/api/knowledge-base/documents?user_id=canonical-b")
    assert response.status_code == 200
    assert response.json()["documents"] == [{"owner_user_id": "canonical-a"}]

    client.cookies.set("knowledge_identity_session", TOKEN_B)
    other_user = client.get("/api/knowledge-base/documents")
    assert other_user.json()["documents"] == [{"owner_user_id": "canonical-b"}]


def test_identity_center_outage_is_reported_as_service_unavailable(monkeypatch):
    class OfflineIdentityClient(FakeIdentityClient):
        async def current_user(self, cookie_value):
            raise IdentityCenterUnavailable

    monkeypatch.setattr(knowledge_app, "get_identity_client", OfflineIdentityClient)
    client = TestClient(knowledge_app.app)
    client.cookies.set("knowledge_identity_session", TOKEN_A)
    response = client.get("/api/auth/me")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "IDENTITY_CENTER_UNAVAILABLE"


def test_unauthenticated_user_cannot_access_business_api(monkeypatch):
    monkeypatch.setattr(knowledge_app, "get_identity_client", FakeIdentityClient)
    client = TestClient(knowledge_app.app)
    response = client.get("/api/knowledge-base/documents")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


def test_logout_clears_browser_cookie_even_when_identity_center_is_offline(monkeypatch):
    monkeypatch.setattr(knowledge_app, "get_identity_client", FakeIdentityClient)
    client = TestClient(knowledge_app.app)
    client.cookies.set("knowledge_identity_session", TOKEN_A)
    response = client.post("/api/auth/logout", headers={"Origin": "http://localhost:3001"})
    assert response.status_code == 503
    assert 'knowledge_identity_session="";' in response.headers["set-cookie"]


def test_cors_allows_exact_local_frontend_origin():
    client = TestClient(knowledge_app.app)
    response = client.options(
        "/api/auth/me",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3001"
    assert response.headers["access-control-allow-credentials"] == "true"
