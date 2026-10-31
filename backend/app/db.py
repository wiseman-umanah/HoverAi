"""Database session management.

Provides an async SQLAlchemy engine, session factory, and a FastAPI
dependency (``get_session``) that yields one session per request.

The engine is created once at module import time using the DATABASE_URL
from settings. The schema itself is owned by Alembic (``alembic/``) — the
app never creates or alters tables at runtime.

Design notes:
  - ``get_session`` commits after the handler returns, but FastAPI runs that
    teardown *after* the response is sent (B6). Routes whose response depends
    on the write being durable (auth) must ``await session.commit()``
    themselves before returning.
  - Long-running routes (``/query``, ``/agent/*``) must not depend on
    ``get_session`` at all: an open session pins a pooled connection for the
    whole STT → vision → TTS run. They open a short ``AsyncSessionLocal()``
    block for their reads instead.
  - SQLite (``sqlite+aiosqlite://``, tests only) gets a ``StaticPool`` so an
    in-memory database is shared by every session instead of each connection
    seeing its own empty database.
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings


def async_database_url(url: str) -> str:
    """Convert a plain Postgres URL to the asyncpg driver scheme.

    Render provides ``postgresql://`` (or legacy ``postgres://``); SQLAlchemy's
    async engine needs ``postgresql+asyncpg://``. Other URLs pass through.

    Args:
        url: Database URL as configured in ``DATABASE_URL``.

    Returns:
        The URL with an async driver scheme.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


_db_url = async_database_url(settings.database_url)

_engine_kwargs: dict[str, Any]
if _db_url.startswith("sqlite"):
    _engine_kwargs = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
else:
    _engine_kwargs = {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}

engine = create_async_engine(
    _db_url,
    echo=False,        # Set True temporarily to debug SQL queries
    **_engine_kwargs,
)

# Session factory — used by get_session and by routes/background tasks that
# need a short-lived session of their own.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    Yields one ``AsyncSession`` per request. On success the session is
    committed automatically; on any unhandled exception it is rolled back
    before being closed, ensuring no partial writes reach the database.

    Note that this teardown runs after the response has been sent, so a
    failing commit here cannot change the status code. Commit explicitly in
    the handler when the client must only see durable results.

    Yields:
        An ``AsyncSession`` bound to the shared engine.

    Raises:
        Re-raises any exception from the route handler after rolling back.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
