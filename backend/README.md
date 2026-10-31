# Hover AI Backend

FastAPI backend for the Hover AI desktop app (Tauri), a voice-guided UI assistant for African languages.

## Stack

- **Python 3.12**, managed by `uv` (isolated `.venv/`)
- **FastAPI** + **uvicorn**: async web framework
- **SQLModel / SQLAlchemy async** on **PostgreSQL** (asyncpg), schema managed by **Alembic**. Tests use in-memory SQLite (aiosqlite)
- **PyJWT** (HS256 access tokens) + bcrypt
- **Groq**:
  - Whisper STT (`whisper-large-v3`)
  - Multimodal vision LLM (`VISION_MODEL`)
  - Orpheus TTS (English voices)
- **Intron Sahara**:
  - TTS for African languages
  - Optional streaming STT (`SAHARA_STT_STREAM_ENABLED`)
- **ffmpeg** (subprocess, only for legacy WebM uploads): v2 clients send 16 kHz mono WAV, used as-is

## Setup

```bash
cd backend/
uv sync                       # install deps into .venv
cp .env.example .env          # fill in DATABASE_URL, JWT_SECRET, INTRON_API_KEY, GROQ_API_KEY
uv run alembic upgrade head   # create/upgrade the schema (same as `uv run setup-db`)
```

ffmpeg is only needed to accept WebM from old (v1) desktop clients
(`apt-get install ffmpeg` / `brew install ffmpeg`). `scripts/setup_local_db.sh` creates a local
Postgres user/db.

## Database migrations (Alembic)

The app never creates tables at startup. The schema lives in `alembic/versions/`;
`0001_baseline` is the full v1 schema. The old SQL files are in `migrations/legacy/` for reference.

```bash
uv run alembic upgrade head                          # apply migrations
uv run alembic revision --autogenerate -m "add x"    # after changing app/models/models.py, then review it
uv run alembic check                                 # fails if models and migrations disagree
uv run alembic stamp 0001_baseline                   # ONCE, for a DB created before Alembic
```

## Commands

```bash
uv run hover-dev          # dev server on :$PORT (default 8000), hot reload, debug logs
uv run hover              # prod server on 0.0.0.0:$PORT (what the Docker image runs)
uv run pytest -q          # tests: no .env or network needed, providers are mocked
uv run ruff check app tests scripts
uv run pyright app        # type check
uv run eval-grounding     # grounding eval on eval/cases (real Groq calls; see eval/README.md)
```

Docs: `http://localhost:8000/docs` (Swagger auto-authorizes after signin/signup).

## Deployment (Docker on Render)

`Dockerfile` (python:3.12-slim + ffmpeg + `uv sync --frozen --no-dev`, non-root) is what Render
builds (`render.yaml`: `runtime: docker`, `rootDir: backend`). Build locally with
`docker build -t hoverai-backend backend`. Migrations are not run by the container. Before
deploying a release with a new migration, run `uv run --no-sync alembic upgrade head` from the
Render Shell (or as a `preDeployCommand` on a paid plan).

## Environment variables

All env vars are read only in `app/config.py`; `.env.example` lists every one.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `JWT_SECRET` | 32+ char secret (`openssl rand -hex 32`) |
| `JWT_EXPIRE_MINUTES` / `JWT_REFRESH_EXPIRE_DAYS` | Access token (default 15 min) / refresh token (default 90 days) lifetime |
| `INTRON_API_KEY` | Sahara TTS + streaming STT |
| `GROQ_API_KEY` | Whisper, vision, Orpheus TTS |
| `VISION_MODEL`, `WHISPER_MODEL`, `TTS_MODEL`, `TTS_VOICE` | Model selection |
| `VISION_JSON_MODE` | Request Groq JSON mode for vision (default true; auto-falls back if the model rejects `response_format`) |
| `VISION_MARKED_MAX_EDGE` | Long-edge cap (px) for set-of-marks screenshots, i.e. requests with a `context` that has elements (default 1280; unmarked screenshots stay 768×432) |
| `TTS_ENABLED`, `SAHARA_TTS_ENABLED`, `SAHARA_STT_STREAM_ENABLED` | Feature flags |
| `TTS_TIMEOUT_S` | Time budget per TTS provider (default 8 s); on timeout `speech_b64` is `""` |
| `PORT`, `LOG_LEVEL` | Server port (Render injects it) and log level |

## Layout

```
app/
├── main.py              # app factory, CORS, lifespan, custom /docs
├── api/v1/router.py     # mounts all routers under /api/v1, /health
├── config.py            # Settings (pydantic-settings)
├── db.py                # async engine, AsyncSessionLocal, get_session()
├── auth.py              # bcrypt, PyJWT, refresh token creation, get_current_user
├── uploads.py           # multipart size caps, screenshot file-or-base64 handling
├── models/models.py     # User, UserSettings, RefreshToken, QueryLog, Conversation, ConversationMessage
├── routers/             # auth, users, query, agent, conversations, languages
├── schemas/             # request/response models
└── services/
    ├── audio.py             # WAV passthrough / ffmpeg → 16 kHz mono WAV
    ├── images.py            # screenshot decode + JPEG compression
    ├── stt.py               # Groq Whisper (+ optional Sahara streaming)
    ├── sahara_stt_stream.py # Sahara WebSocket STT
    ├── vision.py            # screenshot + transcript (+ context) → {summary, steps}
    ├── grounding.py         # screen-context parsing, element list for the prompt, target resolution
    ├── agent.py             # ReAct loop for /agent/* (in-memory sessions)
    ├── conversation.py      # per-user history with rolling summary
    ├── tts.py               # Groq Orpheus
    ├── sahara_tts.py        # Sahara TTS
    └── llm_client.py        # shared Groq client
alembic/                 # Alembic env + versions/ (schema source of truth)
migrations/legacy/       # pre-Alembic SQL, reference only
scripts/eval_grounding.py  # `uv run eval-grounding`: element-hit-rate eval
eval/                    # eval cases + format docs (eval/README.md)
tests/
```

## API (all under `/api/v1`)

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness + DB check (503 `"degraded"` if the DB is unreachable) |
| POST | `/auth/signup`, `/auth/signin` | Returns access + refresh tokens |
| POST | `/auth/refresh` | Rotate refresh token, new access token |
| POST | `/auth/signout` | Revoke refresh token |
| GET/PATCH | `/users/me` | Profile (incl. `language`) |
| GET/PATCH | `/users/me/settings` | Synced app settings |
| POST | `/query` | multipart `audio` + `screenshot` (+ optional `language`, `context`) → transcript, steps (with optional `target`), summary, `speech_b64`, `conversation_id` |
| POST | `/agent/start`, `/agent/turn` | Multi-turn action loop (not yet used by the desktop app) |
| GET | `/agent/info` | Agent limits |
| GET | `/conversations/history` | History grouped by day |
| GET | `/languages` | Supported language codes |

### `/query` (and `/agent/start`, `/agent/turn`) upload contract

- `audio`: file part. v2: `audio/wav` 16 kHz mono PCM16 (used as-is). v1: `audio/webm` (converted
  with ffmpeg). Max 10 MB.
- `screenshot`: file part (`image/jpeg`, long edge ≤ 1600 px) **or** a legacy base64 PNG text field
  (optionally a `data:` URI; Starlette caps text fields at 1 MB). Max 8 MB.
- `context` (`/query` only, optional): text field with a JSON screen context, at most 200 KB. See
  below.
- Errors (`detail` is shown to the user): 400 undecodable/empty audio or screenshot, 413 too large,
  422 nothing heard, 429 STT rate-limited, 502 STT or vision provider failure. TTS failure is not an
  error: `speech_b64` is `""`.

### `/query` grounding: `context` in, `target` out

The desktop draws numbered marks ("set-of-marks") on the interactive elements in the screenshot
and sends the same elements in `context`:

```json
{
  "app": "Code",
  "window_title": "main.rs — HoverAi",
  "window": {"x": 0.0, "y": 0.03, "w": 1.0, "h": 0.97},
  "cursor": {"x": 0.41, "y": 0.62},
  "elements": [
    {"id": 1, "role": "button", "name": "Save", "x": 0.10, "y": 0.20, "w": 0.05, "h": 0.03, "enabled": true}
  ]
}
```

- All coordinates are normalised 0–1 to the screenshot, with x/y the top-left corner. `app`,
  `window_title`, `window` and `cursor` may be null. Element `id`s are the numbers drawn on the
  screenshot.
- Validation is lenient. Values are clipped to [0, 1] and names are truncated. Only the first
  200 elements are kept. Invalid, zero-area or duplicate-id elements are dropped one by one. A
  context that is oversized, not JSON or not an object is ignored with a log warning, and the
  request still succeeds.
- Elements may carry `"focused": true` (sent only when true) for the element with keyboard
  focus. It is shown to the model as `(focused)` after that element's entry.
- Disabled elements are left out of the prompt's element list, but ids are never renumbered.

Each response step gets an optional `target`:

```json
{"step": 1, "instruction": "...", "keys": null, "tip": null,
 "target": {"element_id": 12, "x": 0.10, "y": 0.20, "w": 0.05, "h": 0.03}}
```

- `element_id` set: the model picked mark 12. The server copies x/y/w/h from the request's
  element and never uses model coordinates.
- `element_id: null`: the model gave a fallback bbox for something unmarked. It is clipped to
  [0, 1]. Boxes with w or h ≤ 0 or > 0.9, or values clearly not normalised, are rejected.
- `target: null`: nothing on screen to point at, such as a keyboard shortcut, a wait, or a thing
  that only appears after an earlier step. Unknown ids also give `null` (and are logged).
- Old clients that send no `context` still work. The model is only asked for bbox targets, as a
  best effort.

Logic: `app/services/grounding.py` (`parse_screen_context`, `render_context`,
`resolve_targets`). The vision call uses Groq JSON mode (`VISION_JSON_MODE`), with one retry
without `response_format` if the model rejects it. The tolerant parser and the single
"Invalid JSON" retry remain as fallbacks.
