"""Knowledge configuration; portable clients never read environment variables."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    gateway_base_url: str
    vector_base_url: str
    vector_api_key: str = field(repr=False)
    collection: str
    embedding_model: str
    context_api_key: str = field(repr=False)
    credential_encryption_key: str = field(repr=False)
    identity_base_url: str
    database_url: str = field(repr=False)
    cors_origins: tuple[str, ...]
    database_pool_size: int
    database_max_overflow: int
    host: str
    port: int
    start_embedding_worker: bool
    embedding_workers: int
    cookie_secure: bool
    cookie_samesite: str


def get_settings() -> Settings:
    """Resolve optional legacy names; missing capabilities are validated on use."""
    base = os.getenv("GATEWAY_BASE_URL", "").strip()
    return Settings(
        gateway_base_url=base,
        vector_base_url=(os.getenv("GATEWAY_VECTOR_BASE_URL") or base).strip(),
        vector_api_key=os.getenv("GATEWAY_VECTOR_API_KEY") or os.getenv("GATEWAY_API_KEY", ""),
        collection=(os.getenv("GATEWAY_VECTOR_COLLECTION") or os.getenv("GATEWAY_COLLECTION", "")).strip(),
        embedding_model=os.getenv("GATEWAY_EMBEDDING_MODEL", "").strip(),
        context_api_key=os.getenv("GATEWAY_CONTEXT_API_KEY", "").strip(),
        credential_encryption_key=os.getenv("GATEWAY_CREDENTIAL_ENCRYPTION_KEY", "").strip(),
        identity_base_url=os.getenv("IDENTITY_CENTER_BASE_URL", "").strip(),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        cors_origins=tuple(x.strip() for x in os.getenv("CORS_ALLOW_ORIGINS",
            "http://localhost:3001,http://127.0.0.1:3001,http://localhost:3000,http://127.0.0.1:3000").split(",") if x.strip()),
        database_pool_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
        database_max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "10")),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8001")),
        start_embedding_worker=os.getenv("KNOWLEDGE_START_EMBEDDING_WORKER", "1").strip() != "0",
        embedding_workers=max(1, min(16, int(os.getenv("KNOWLEDGE_EMBEDDING_WORKERS", "3")))),
        cookie_secure=os.getenv("AUTH_COOKIE_SECURE", str(os.getenv("APP_ENV", "development").lower() == "production")).strip().lower() == "true",
        cookie_samesite=os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower(),
    )
