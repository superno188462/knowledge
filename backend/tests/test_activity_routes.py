"""Operation logs remain available and user-scoped after module extraction."""
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from app.bootstrap import app
from app.modules.auth.dependencies import require_current_user
from app.integrations.identity.client import IdentityUser
from app.modules.activity_logs import router as logs


def test_log_route_uses_verified_user_and_requires_no_gateway_key(monkeypatch):
    reader = AsyncMock(return_value={"logs": [], "page": 1, "page_size": 25, "total": 0})
    monkeypatch.setattr(logs, "list_activity", reader)
    app.dependency_overrides[require_current_user] = lambda: IdentityUser("verified", "test", "user")
    try:
        response = TestClient(app).get("/api/knowledge-base/activity-logs?user_id=other&path_query=a.md")
        assert response.status_code == 200
        assert reader.call_args.args == ("verified",)
        assert reader.call_args.kwargs["path_query"] == "a.md"
    finally:
        app.dependency_overrides.pop(require_current_user, None)
