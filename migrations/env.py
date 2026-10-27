"""Alembic-only connection setup; not application session/pool management."""

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from investigation_engine.infrastructure.persistence.models import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations(connection: Connection) -> None:
    if connection.dialect.server_version_info < (15,):
        raise RuntimeError("This schema requires PostgreSQL 15+ for NULLS NOT DISTINCT")
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Offline SQL generation does not require database settings.
    from investigation_engine.infrastructure.config import settings

    # A migration has one short-lived connection; no application pool policy.
    engine = create_async_engine(
        settings.database_url(), poolclass=pool.NullPool, hide_parameters=True,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # psycopg async I/O requires a selector loop on Windows. Python 3.14's
    # loop_factory avoids the deprecated global event-loop policy API.
    asyncio.run(
        run_migrations_online(),
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None,
    )
