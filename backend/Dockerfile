# Hover AI backend — production image (Render, runtime: docker).
#
# - python:3.12-slim pins the interpreter (matches requires-python / .python-version).
# - ffmpeg is only needed for legacy v1 clients that upload WebM; v2 clients send
#   16 kHz WAV which is used as-is.
# - Dependencies are installed from uv.lock (--frozen) in a layer that only
#   changes when pyproject.toml / uv.lock change.
# - Runs as an unprivileged user. Binds 0.0.0.0:$PORT (Render injects PORT).
# - Migrations are NOT run on start; run `uv run --no-sync alembic upgrade head`
#   as a pre-deploy step or one-off job (see render.yaml / README).

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Dependencies first (cached unless the lockfile changes)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Application code
COPY app ./app
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin hover \
    && chown -R hover:hover /app
USER hover

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "hover"]
