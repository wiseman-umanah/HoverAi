"""FastAPI application entry point.

Responsibilities (wiring only — no business logic):
  - Create the FastAPI application instance.
  - Register CORS middleware.
  - Manage the application lifespan (no DB work at startup — the schema is
    owned by Alembic; run ``uv run alembic upgrade head`` before deploying).
  - Mount the versioned API router at /api/v1.
  - Serve a custom /docs that auto-authorizes after sign-in.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.v1.router import router as v1_router
from app.config import settings
from app.db import engine

# In dev (LOG_LEVEL=DEBUG or running hover-dev), show debug logs from app code.
# In production uvicorn sets its own handlers; we just set the level here.
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of shared resources.

    Startup:
        - Nothing. Tables are no longer created here (B14): ``create_all``
          never alters existing tables and silently diverged from the real
          schema. Run ``uv run alembic upgrade head`` instead.

    Shutdown:
        - Disposes the engine connection pool.

    Args:
        application: The FastAPI application instance (unused but required
            by the lifespan protocol).

    Yields:
        None — control returns to FastAPI while the app is running.
    """
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Returns:
        A fully configured FastAPI instance ready to be served by uvicorn.
    """
    application = FastAPI(
        title="Hover AI API",
        version="0.1.0",
        description=(
            "Voice-guided UI assistant for African languages.\n\n"
            "**Quick start in Swagger:**\n"
            "1. Call `POST /api/v1/auth/signup` or `POST /api/v1/auth/signin`.\n"
            "2. The Bearer token is **automatically applied** — no manual copy-paste needed.\n"
            "3. All protected endpoints are immediately accessible."
        ),
        lifespan=lifespan,
        # Disable default /docs so we can serve our custom version below
        docs_url=None,
        redoc_url="/redoc",
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Root info route ───────────────────────────────────────────────────────
    @application.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        """Return a human-friendly summary of the API."""
        return JSONResponse({
            "name": "Hover AI API",
            "version": "0.1.0",
            "description": (
                "Voice-guided UI assistant — African language STT with "
                "Intron Sahara STT/TTS, Groq Whisper STT and Groq vision."
            ),
            "docs": "/docs",
            "health": "/api/v1/health",
            "endpoints": {
                "signup":   "POST /api/v1/auth/signup",
                "signin":   "POST /api/v1/auth/signin",
                "refresh":  "POST /api/v1/auth/refresh",
                "signout":  "POST /api/v1/auth/signout",
                "me":       "GET  /api/v1/users/me",
                "settings": "GET|PATCH /api/v1/users/me/settings",
                "query":    "POST /api/v1/query",
            },
        })

    # ── Custom /docs with auto-authorize JS ───────────────────────────────────
    @application.get("/docs", include_in_schema=False)
    async def custom_swagger_ui() -> HTMLResponse:
        """Serve Swagger UI with a JS hook that auto-authorizes after sign-in.

        After any successful call to ``/signin`` or ``/signup``, the injected
        script extracts ``access_token`` from the response body and calls
        ``ui.preauthorizeApiKey`` so the Bearer token is stored in Swagger
        without the user needing to open the Authorize dialog.
        """
        # Get the default Swagger HTML from FastAPI
        base_html = get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="Hover AI API — Swagger UI",
            swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
        )
        raw_body = base_html.body
        html = (
            raw_body.decode()
            if isinstance(raw_body, (bytes, bytearray))
            else bytes(raw_body).decode()
        )

        # Inject our auto-authorize script just before </body>
        auto_auth_script = """
<script>
// Auto-authorize Swagger UI after a successful signin or signup call.
// Waits for the SwaggerUI instance to be ready, then patches the
// response interceptor to detect auth tokens and store them automatically.
(function waitForSwagger() {
  const interval = setInterval(function () {
    if (typeof window.ui === 'undefined') return;
    clearInterval(interval);

    const originalResponseInterceptor = window.ui.getConfigs().responseInterceptor;

    window.ui.initOAuth({});  // ensure oauth2 state is initialised

    // Patch SwaggerUI's response pipeline
    window.ui.setConfigs({
      responseInterceptor: function (response) {
        try {
          // Only act on auth endpoints that return a token
          const url = response.url || '';
          const isAuthCall = url.includes('/auth/signin') || url.includes('/auth/signup') || url.includes('/auth/refresh');
          if (isAuthCall && response.status === 200 || (isAuthCall && response.status === 201)) {
            const body = typeof response.body === 'string'
              ? JSON.parse(response.body)
              : response.body;
            if (body && body.access_token) {
              // Store in SwaggerUI's auth state — equivalent to clicking
              // "Authorize" and pasting the token manually
              window.ui.preauthorizeApiKey('OAuth2PasswordBearer', body.access_token);
              window.ui.preauthorizeApiKey('HTTPBearer', body.access_token);
              console.log('[Hover AI] Bearer token auto-applied to Swagger UI');

              // Also show a brief banner so the user knows it worked
              const banner = document.createElement('div');
              banner.textContent = '✓ Authorized — Bearer token applied automatically';
              banner.style.cssText = [
                'position:fixed', 'top:12px', 'right:16px', 'z-index:9999',
                'background:#49cc90', 'color:#fff', 'padding:8px 14px',
                'border-radius:4px', 'font:13px/1.4 sans-serif',
                'box-shadow:0 2px 8px rgba(0,0,0,.2)', 'pointer-events:none'
              ].join(';');
              document.body.appendChild(banner);
              setTimeout(function () { banner.remove(); }, 3500);
            }
          }
        } catch (_) {
          // Never crash the interceptor — silently ignore parse errors
        }
        return originalResponseInterceptor
          ? originalResponseInterceptor(response)
          : response;
      }
    });
  }, 200);
})();
</script>
"""
        html = html.replace("</body>", auto_auth_script + "\n</body>")
        return HTMLResponse(html)

    # ── API versioning ────────────────────────────────────────────────────────
    application.include_router(v1_router, prefix="/api/v1")

    return application


app = create_app()
