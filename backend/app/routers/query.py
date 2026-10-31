"""Query route — POST /api/v1/query.

Main feature endpoint. Accepts audio + screenshot from the desktop app,
transcribes with Groq Whisper (~1-2s), runs the vision pipeline,
returns guided steps + TTS in ~5-15s.

Request contract (multipart/form-data):
  - ``audio``: file part — ``audio/wav`` 16 kHz mono (v2) or ``audio/webm`` (v1).
  - ``screenshot``: file part (v2, ``image/jpeg``) or legacy base64 PNG text
    field (v1). See ``app/uploads.py``.
  - ``language``: optional text field; defaults to the user's saved language.
  - ``context``: optional text field, JSON ``ScreenContext`` (≤ 200 KB): app,
    window title, window/cursor and up to 200 accessibility elements whose
    ids are drawn on the screenshot (set-of-marks). Oversized or malformed
    context is logged and ignored — it never fails the request. See
    ``app/schemas/vision.py`` and ``app/services/grounding.py``.

Response steps carry an optional ``target`` box (normalised 0–1) for the
overlay beacon; element targets use the request's element bounds, never
model-generated coordinates.

Size caps: audio ≤ 10 MB, screenshot ≤ 8 MB → 413. Error mapping is in
``app/routers/common.py``; vision failures → 502. TTS failures never fail the
request — ``speech_b64`` is just empty.

No request-scoped DB session is held (B6): the voice preference is read in a
short session up front, and the DB writes run as background tasks after the
response is sent, each with its own session.
"""

import asyncio
import logging
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from app.auth import get_current_user
from app.db import AsyncSessionLocal
from app.models.models import QueryLog, User
from app.routers.common import SCREENSHOT_UNDECODABLE_DETAIL, load_voice_gender, transcribe
from app.schemas.query import QueryResponse
from app.schemas.users import VOICE_GENDER_MAP
from app.services.conversation import get_or_create_conversation, save_turn
from app.services.grounding import parse_screen_context
from app.services.images import InvalidImageError
from app.services.tts import speak
from app.services.vision import VisionParseError, _compress_screenshot, run_vision
from app.uploads import read_audio, read_screenshot

logger = logging.getLogger(__name__)

router = APIRouter()


async def _write_query_log(log_kwargs: dict[str, Any]) -> None:
    """Background task: write QueryLog to DB after the response is sent.

    Runs entirely after the HTTP response has been sent — nothing in the
    hot path waits for this.  Uses its own session from AsyncSessionLocal.

    Args:
        log_kwargs: Keyword arguments passed directly to QueryLog().
    """
    async with AsyncSessionLocal() as session:
        log = QueryLog(**log_kwargs)
        session.add(log)
        await session.commit()
        logger.info("QueryLog written — id=%s", log.id)


@router.post("", response_model=QueryResponse)
async def query(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(
        ..., description="Voice recording: audio/wav 16 kHz mono (v2) or audio/webm (v1)"
    ),
    screenshot: UploadFile | str = File(
        ...,
        description=(
            "Screenshot as a file part (image/jpeg, long edge ≤ 1600 px) — or, for "
            "legacy clients, a base64 PNG text field (optionally a data: URI)"
        ),
    ),
    language: str = Form(default="", description="Hover AI language code — defaults to user's saved preference"),
    context: str = Form(
        default="",
        description=(
            "Optional JSON screen context (app, window_title, window, cursor, elements[]) "
            "≤ 200 KB; element ids match the marks drawn on the screenshot. Ignored if malformed."
        ),
    ),
    current_user: User = Depends(get_current_user),
) -> QueryResponse:
    """Run the voice-to-guided-steps pipeline with TTS response.

    Hot path (blocks until response is sent):
        1. Read uploads (size-capped), parse the optional screen context,
           resolve language + voice preference.
        2. Audio normalisation + STT ‖ screenshot compression ‖ conversation history.
        3. Vision pipeline (Groq multimodal).
        4. TTS (Sahara for African languages, Groq Orpheus otherwise), time-boxed.
        5. Return response.

    Background (fires after response, non-blocking):
        • Write QueryLog and the conversation turn to the DB.

    Raises:
        HTTPException 400: Audio or screenshot could not be decoded.
        HTTPException 413: Audio > 10 MB or screenshot > 8 MB.
        HTTPException 422: STT succeeded but heard no speech.
        HTTPException 429: STT provider rate-limited.
        HTTPException 502: STT provider or vision LLM failed.
    """
    # ── 1. Uploads + language + voice preference ──────────────────────────────
    audio_bytes = await read_audio(audio)
    image_bytes = await read_screenshot(screenshot)
    screen_context = parse_screen_context(context)
    marked = bool(screen_context and screen_context.elements)

    # Language priority: form field (client override) → user's saved preference
    effective_language = language.strip() or current_user.language or "en-pidgin"
    voice_gender = await load_voice_gender(current_user.id)
    tts_voice = VOICE_GENDER_MAP.get(voice_gender, "autumn")

    # ── 2. STT ‖ screenshot compression ‖ conversation history ────────────────
    stt_res, compressed_res, conv_res = await asyncio.gather(
        transcribe(audio_bytes, effective_language),
        asyncio.to_thread(_compress_screenshot, image_bytes, marked),
        get_or_create_conversation(current_user.id),
        return_exceptions=True,
    )
    # Audio errors take precedence: they're the most actionable for the user.
    if isinstance(stt_res, BaseException):
        raise stt_res
    if isinstance(compressed_res, InvalidImageError):
        logger.warning("Screenshot decode failed: %s", compressed_res)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=SCREENSHOT_UNDECODABLE_DETAIL
        )
    if isinstance(compressed_res, BaseException):
        raise compressed_res
    if isinstance(conv_res, BaseException):
        raise conv_res
    stt_result, compressed_b64, conv_ctx = stt_res, compressed_res, conv_res

    # ── 3. Vision pipeline ────────────────────────────────────────────────────
    try:
        vision_result = await run_vision(
            image_bytes,
            stt_result.transcript,
            effective_language,
            compressed_b64=compressed_b64,
            history=conv_ctx.recent_messages or None,
            rolling_summary=conv_ctx.rolling_summary,
            context=screen_context,
        )
    except VisionParseError as exc:
        logger.error("Vision pipeline failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't work out the steps for that — please try again.",
        ) from exc
    except Exception as exc:
        logger.error("Vision pipeline unexpected error: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The AI service is unavailable right now — please try again.",
        ) from exc

    # ── 4. TTS (bounded by settings.tts_timeout_s per provider) ───────────────
    speech_b64 = await speak(
        vision_result.summary,
        effective_language,
        voice_gender=voice_gender,
        orpheus_voice=tts_voice,
    )

    # ── 5. Return response (DB writes happen after this) ─────────────────────
    steps_list = [s.model_dump() for s in vision_result.steps]
    background_tasks.add_task(
        _write_query_log,
        {
            "user_id": current_user.id,
            "language_used": effective_language,
            "transcript_whisper": stt_result.transcript,
            "whisper_latency_ms": stt_result.whisper_latency_ms,
            "vision_response": {"summary": vision_result.summary, "steps": steps_list},
            "beacon_steps": steps_list,
        },
    )
    background_tasks.add_task(
        save_turn,
        current_user.id,
        conv_ctx.conversation_id,
        stt_result.transcript,
        vision_result.summary,
    )

    return QueryResponse(
        transcript=stt_result.transcript,
        steps=vision_result.steps,
        summary=vision_result.summary,
        speech_b64=speech_b64,
        conversation_id=conv_ctx.conversation_id,
    )
