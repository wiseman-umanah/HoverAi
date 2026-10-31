"""Project entry-point scripts.

Registered in pyproject.toml [project.scripts] so they are available as
console commands inside the .venv:

  uv run hover       — production server (no reload, info-level logging)
  uv run hover-dev   — development server (hot reload, debug logging)

The port comes from ``settings.port`` (env ``PORT``, default 8000) because
Render and Docker hosts inject the port to bind at runtime.
"""

import uvicorn

from app.config import settings


def run() -> None:
    """Start the production uvicorn server.

    Binds to all interfaces on ``$PORT`` (default 8000). Hot reload and
    debug logging are disabled; ``--proxy-headers`` handling is on so client
    IPs/schemes from Render's proxy are respected. Use this for staging and production deployments.
    """
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=False,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


def run_dev() -> None:
    """Start the development uvicorn server with hot reload and debug logging.

    Watches all Python files under the current directory and reloads on
    change. Enables debug-level logging so SQL queries and internal events
    are visible. Do not use in production.
    """
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=True,
        log_level="debug",
    )
