"""HTTP 请求与响应契约。"""
from typing import Any
from pydantic import BaseModel, Field, SecretStr

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


