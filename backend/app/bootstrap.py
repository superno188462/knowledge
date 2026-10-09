"""创建 HTTP 应用，在同一事件循环中管理持久化嵌入队列。"""
import asyncio
from contextlib import asynccontextmanager, suppress
from app.modules.embedding_jobs.worker import embedding_queue
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.settings import get_settings

@asynccontextmanager
async def lifespan(application: FastAPI):
    """启动有并发上限的异步 worker；关闭时取消任务，未完成任务由租约恢复。"""
    task = None
    if get_settings().start_embedding_worker:
        task = asyncio.create_task(embedding_queue.serve(), name="knowledge-embedding-queue")
    application.state.embedding_worker_task = task
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        application.state.embedding_worker_task = None


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="Knowledge Data Platform API", version="1.0.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    from app.modules.auth.router import router as auth
    from app.modules.credentials.router import router as credentials
    from app.modules.knowledge.router import router as knowledge
    from app.modules.embedding_jobs.router import router as jobs
    from app.modules.user_context.router import router as context
    from app.modules.activity_logs.router import router as logs
    for router in (auth, credentials, knowledge, jobs, context, logs):
        application.include_router(router)
    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "knowledge-data-platform"}
    return application

app = create_app()
