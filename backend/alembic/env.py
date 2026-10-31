"""Alembic environment — async engine, URL from app settings.

Design decisions:
  - The database URL comes from ``app.config.settings.database_url`` (i.e.
    ``DATABASE_URL`` in ``.env`` / the environment) — the same single source
    the app uses, converted to the asyncpg driver by ``async_database_url``.
    ``alembic.ini`` deliberately has no ``sqlalchemy.url``.
  - ``target_metadata`` is ``SQLModel.metadata`` with every model imported,
    so ``alembic revision --autogenerate`` and ``alembic check`` compare the
    live schema against ``app/models/models.py``.
  - Migrations run on an async engine (asyncpg) via ``run_sync``, matching
    the runtime driver so there is no second (psycopg) dependency.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlmodel import SQLModel

from alembic import context
from app.config import settings
from app.db import async_database_url
from app.models import models as _models  # noqa: F401 — registers tables on SQLModel.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", async_database_url(settings.database_url))

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout, no DB connection).

    Usage: ``uv run alembic upgrade head --sql``.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure the context on a sync connection facade and run migrations.

    Args:
        connection: Sync-style connection provided by ``run_sync``.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations on one connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the configured database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
