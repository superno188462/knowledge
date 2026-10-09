from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from app.settings import get_settings
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.infrastructure.database import Base
from app.modules.knowledge import models as knowledge_models  # noqa: F401
from app.modules.embedding_jobs import models as job_models  # noqa: F401
from app.modules.activity_logs import models as log_models  # noqa: F401
from app.modules.credentials import models as gateway_credential_models  # noqa: F401

config = context.config
database_url = get_settings().database_url
if not database_url:
    raise RuntimeError("DATABASE_URL_REQUIRED")
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
