"""模块 HTTP 路由；URL 与原版本一致。"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from app.integrations.identity.client import IdentityUser
from app.modules.auth.dependencies import require_current_user
from .service import list_activity

router = APIRouter()

@router.get("/api/knowledge-base/activity-logs", summary="分页读取当前用户的文档与嵌入日志")
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


