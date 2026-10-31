"""Tests for GET /api/v1/health (B15)."""

import httpx

from app.api.v1 import router as v1_router


async def test_health_ok(client: httpx.AsyncClient):
    """A reachable DB → 200 with status ok."""
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "0.1.0", "db": "ok"}


async def test_health_db_down_returns_503(client: httpx.AsyncClient, monkeypatch):
    """A failing DB ping → 503 with status degraded."""

    async def broken_ping() -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(v1_router, "_db_ping", broken_ping)
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
    assert resp.json()["db"] == "unavailable"
