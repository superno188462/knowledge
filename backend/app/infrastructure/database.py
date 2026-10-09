"""异步数据库连接与 ORM 基类。"""

from __future__ import annotations

from app.settings import get_settings
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL_REQUIRED")
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("DATABASE_URL must use postgresql+asyncpg")
    return url


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        create_async_engine(
            _database_url(),
            pool_pre_ping=True,
            pool_size=get_settings().database_pool_size,
            max_overflow=get_settings().database_max_overflow,
        ),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db_session():
    factory = get_session_factory()
    async with factory() as session:
        yield session
