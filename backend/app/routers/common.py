"""Helpers shared by the long-running pipeline routes (/query, /agent/*).

Two concerns live here so both routers behave identically:

  - **Session lifetime (B6).** ``load_voice_gender`` reads the user's settings
    in a short ``AsyncSessionLocal()`` block that is closed before any
    provider call, instead of holding a request-scoped session (and its pool
    connection) through STT → vision → TTS.
  - **Error mapping (B7).** ``transcribe`` turns audio/STT failures into
    distinct, human-readable HTTP errors — the desktop shows ``detail`` as-is:

    ============================  ======  =====================================
    Failure                       Status  detail
    ============================  ======  =====================================
    empty/undecodable audio       400     Audio could not be decoded.
    STT provider rate limit       429     Speech service is busy…
    STT provider error/timeout    502     Speech recognition is unavailable…
    provider OK, no speech        422     I didn't catch that — please try again.
    ============================  ======  =====================================
"""

import logging
import uuid

from fastapi import HTTPException, status
from sqlmodel import select

from app.db import AsyncSessionLocal
from app.models.models import UserSettings
from app.schemas.stt import STTResult
from app.services.audio import AudioConversionError, convert_to_wav
from app.services.stt import STTProviderError, run_stt

logger = logging.getLogger(__name__)

AUDIO_UNDECODABLE_DETAIL = "Audio could not be decoded."
EMPTY_TRANSCRIPT_DETAIL = "I didn't catch that — please try again."
STT_UNAVAILABLE_DETAIL = "Speech recognition is unavailable right now — please try again."
STT_RATE_LIMITED_DETAIL = "Speech service is busy — please wait a moment and try again."
SCREENSHOT_UNDECODABLE_DETAIL = "Screenshot could not be decoded."


async def load_voice_gender(user_id: uuid.UUID) -> str:
    """Read the user's TTS voice gender with a short-lived session.

    Args:
        user_id: The authenticated user's id.

    Returns:
        ``"female"`` or ``"male"`` (defaults to ``"female"``).
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(UserSettings.voice_gender).where(UserSettings.user_id == user_id)
        )
        voice_gender = result.scalar_one_or_none()
    return voice_gender or "female"


async def transcribe(audio_bytes: bytes, language: str) -> STTResult:
    """Normalise audio and run STT, mapping failures to HTTP errors.

    Args:
        audio_bytes: Raw uploaded audio (WAV or legacy WebM).
        language: Hover AI language code.

    Returns:
        An ``STTResult`` with a non-empty transcript.

    Raises:
        HTTPException 400: Audio is empty or undecodable.
        HTTPException 422: The provider heard no speech.
        HTTPException 429: The STT provider rate-limited us.
        HTTPException 502: The STT provider failed or timed out.
    """
    try:
        wav_bytes = await convert_to_wav(audio_bytes)
    except AudioConversionError as exc:
        logger.warning("Audio conversion failed: %s", exc.message)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=AUDIO_UNDECODABLE_DETAIL
        ) from exc

    try:
        result = await run_stt(audio_bytes, wav_bytes, language)
    except STTProviderError as exc:
        if exc.rate_limited:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=STT_RATE_LIMITED_DETAIL,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=STT_UNAVAILABLE_DETAIL
        ) from exc

    if not result.transcript.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=EMPTY_TRANSCRIPT_DETAIL,
        )
    return result
