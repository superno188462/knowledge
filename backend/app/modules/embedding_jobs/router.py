"""模块 HTTP 路由；URL 与原版本一致。"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from app.integrations.identity.client import IdentityUser
from app.modules.auth.dependencies import require_current_user
from .worker import embedding_queue

router = APIRouter()

@router.get("/api/knowledge-base/embedding-jobs", summary="读取当前用户最近的嵌入队列任务")
async def list_embedding_jobs(
    limit: int = Query(default=50, ge=1, le=100),
    user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    return {"jobs": await embedding_queue.list_for_user(user.id, limit)}


@router.get("/api/knowledge-base/embedding-jobs/{job_id}", summary="读取当前用户的嵌入任务状态")
async def get_embedding_job(
    job_id: str, user: IdentityUser = Depends(require_current_user),
) -> dict[str, Any]:
    job = await embedding_queue.get_for_user(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "KNOWLEDGE_JOB_NOT_FOUND"})
    return job


