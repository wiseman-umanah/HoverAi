"""Shared pytest fixtures for the Hover AI backend test suite.

Design (T1/T2):
  - Environment is set *before* ``app`` is imported: the app's own engine is
    pointed at in-memory SQLite (``app/db.py`` gives SQLite a ``StaticPool``,
    so every session shares one connection and therefore one database), and
    provider keys get dummy values so a real ``.env`` is neither needed (CI)
    nor ever used (no test can reach Groq/Intron with real credentials).
  - Because the app engine itself is the test database, there are no
    dependency overrides: ``get_session``, ``get_current_user`` and the
    short-lived ``AsyncSessionLocal()`` blocks in /query all hit the same DB.
  - One event loop for the whole run (``pytest.ini`` sets session loop scope)
    and ``httpx.AsyncClient`` + ``ASGITransport`` instead of ``TestClient``,
    which would run the app on a second loop in a worker thread.
  - Tables are created once from ``SQLModel.metadata``; every table is
    emptied after each test for isolation.
"""

import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["JWT_SECRET"] = "test-secret-that-is-at-least-32-bytes-long"
os.environ["GROQ_API_KEY"] = "test-groq-key"
os.environ["INTRON_API_KEY"] = "test-intron-key"
os.environ["SAHARA_STT_STREAM_ENABLED"] = "false"

from collections.abc import AsyncGenerator  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import models as _models  # noqa: E402, F401 — register tables


@pytest.fixture(scope="session", autouse=True)
async def create_test_tables() -> AsyncGenerator[None, None]:
    """Create all tables in the in-memory SQLite database once per test session."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncGenerator[None, None]:
    """Delete every row after each test so tests never see each other's data."""
    yield
    async with engine.begin() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest.fixture()
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Return an async HTTP client that calls the ASGI app in-process.

    The app lifespan is not run (``ASGITransport`` doesn't send lifespan
    events), which is fine — it does no DB work.

    Yields:
        An ``httpx.AsyncClient`` with base URL ``http://testserver``.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _signup(client: httpx.AsyncClient, email: str = "user@example.com") -> dict[str, str]:
    """Create a user and return the token response body.

    Args:
        client: The test HTTP client.
        email: Email for the new account.

    Returns:
        The JSON body with ``access_token`` and ``refresh_token``.
    """
    resp = await client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "full_name": "Test User",
            "password": "strongpassword1",
            "language": "en",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture()
def signup():
    """Return the ``_signup`` helper so tests can create users.

    Returns:
        ``async (client, email) -> token response dict``.
    """
    return _signup


@pytest.fixture()
async def auth_headers(client: httpx.AsyncClient) -> dict[str, str]:
    """Sign up a user and return an ``Authorization`` header for it.

    Args:
        client: The test HTTP client.

    Returns:
        ``{"Authorization": "Bearer <access token>"}``.
    """
    tokens = await _signup(client, "query-user@example.com")
    return {"Authorization": f"Bearer {tokens['access_token']}"}
