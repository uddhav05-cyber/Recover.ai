"""Alembic environment — async, driven by the app's engine factory.

The DB URL and TLS handling come from ``app.db.session.make_async_engine`` so
migrations connect exactly like the app does (important for hosted Postgres).
All models are imported so ``Base.metadata`` is fully populated for autogenerate.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db import models  # noqa: F401  (register models on Base.metadata)
from app.db.base import Base
from app.db.session import make_async_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = make_async_engine(poolclass=NullPool, pool_pre_ping=False)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().async_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
