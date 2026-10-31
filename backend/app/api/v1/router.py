"""API v1 router — aggregates all v1 endpoints under /api/v1.

Adding a v2 in the future is as simple as creating app/api/v2/router.py
and mounting it in main.py alongside this one.
"""

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import engine
from app.routers import agent, auth, conversations, languages, query, users

logger = logging.getLogger(__name__)

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(users.router, prefix="/users", tags=["users"])
router.include_router(query.router, prefix="/query", tags=["query"])
router.include_router(agent.router, prefix="/agent", tags=["agent"])
router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
router.include_router(languages.router, prefix="/languages", tags=["languages"])

# A health probe must answer quickly even if the DB hangs.
_HEALTH_DB_TIMEOUT_S = 3.0


async def _db_ping() -> None:
    """Run ``SELECT 1`` on a fresh pooled connection.

    Raises:
        Exception: Any driver/connection error.
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


@router.get("/health", tags=["meta"])
async def health() -> JSONResponse:
    """Return service liveness and database connectivity status.

    Runs a lightweight ``SELECT 1`` against the database (with a short
    timeout) so the endpoint reflects both application and database health.
    A failing DB check returns **HTTP 503** with ``status: "degraded"`` so
    load balancers and uptime monitors actually notice (B15).

    Returns:
        A JSON object with ``status``, ``version``, and ``db`` fields.
    """
    try:
        await asyncio.wait_for(_db_ping(), timeout=_HEALTH_DB_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — intentional catch-all for health check
        logger.warning("Health check DB ping failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "version": "0.1.0", "db": "unavailable"},
        )
    return JSONResponse({"status": "ok", "version": "0.1.0", "db": "ok"})
