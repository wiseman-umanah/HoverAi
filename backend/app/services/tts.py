"""Text-to-speech service — converts LLM summary text to spoken audio.

Uses Groq's Orpheus v1 English TTS model (canopylabs/orpheus-v1-english)
via the OpenAI-compatible speech endpoint. Returns raw WAV bytes which the
caller base64-encodes and sends to the desktop app for playback.

Orpheus is a high-quality neural TTS model that runs on Groq's infrastructure.
It supports six voices and outputs WAV format only.

``speak`` is the entry point used by /query: it picks Sahara (African
languages) or Orpheus and enforces a time budget per provider (B10) —
Sahara's enqueue/poll/download flow alone could otherwise take ~60 s. When
both providers fail or time out the caller gets ``""`` and still returns the
steps; audio is a nice-to-have, never a reason to fail the request.
"""

import asyncio
import base64
import logging
import time

from openai import AsyncOpenAI

from app.config import settings
from app.services.sahara_tts import SAHARA_VOICE_MAP, synthesise_sahara

logger = logging.getLogger(__name__)

# Groq OpenAI-compatible client for TTS (shared, thread-safe)
# max_retries=1 + timeout=20s: TTS can be slow for long texts; cap retries
# to stay within ngrok's 30s free-tier window.
_groq_tts_client = AsyncOpenAI(
    api_key=settings.groq_api_key,
    base_url="https://api.groq.com/openai/v1",
    max_retries=1,
    timeout=20.0,
)

# Valid voices for canopylabs/orpheus-v1-english (confirmed from API error response)
ORPHEUS_VOICES = ("autumn", "diana", "hannah", "austin", "daniel", "troy")


class TTSError(Exception):
    """Raised when the TTS API call fails.

    Attributes:
        message: Human-readable description of what went wrong.
    """

    def __init__(self, message: str) -> None:
        """Initialise with a descriptive error message.

        Args:
            message: Description of the TTS failure.
        """
        super().__init__(message)
        self.message = message


async def synthesise(text: str, voice: str | None = None) -> tuple[str, int]:
    """Convert text to speech using Groq Orpheus and return base64 WAV.

    Calls ``canopylabs/orpheus-v1-english`` with the resolved Orpheus voice
    name. The caller should resolve the user's ``voice_gender`` preference to
    a concrete voice name via ``VOICE_GENDER_MAP`` before calling this function.
    Falls back to ``settings.tts_voice`` if no voice is supplied.

    The response is raw WAV bytes which are base64-encoded for safe embedding
    in a JSON response body. The caller attaches the result to
    ``QueryResponse.speech_b64`` so the desktop app can decode and play it without a
    second round-trip.

    Args:
        text: The text to synthesise — typically the LLM ``summary`` string
              from the vision pipeline (≤ ~200 chars for best latency).
        voice: Concrete Orpheus voice name (e.g. ``"autumn"``, ``"daniel"``).
               Defaults to ``settings.tts_voice`` when ``None``.

    Returns:
        A tuple of ``(speech_b64, elapsed_ms)`` where ``speech_b64`` is a
        base64-encoded WAV string and ``elapsed_ms`` is the round-trip time.
        Returns ``("", elapsed_ms)`` on any failure so the query route can
        still return beacon steps even if TTS fails.

    Raises:
        Does not raise — all exceptions are caught and logged. The empty
        string sentinel lets the caller degrade gracefully.
    """
    start = time.monotonic()

    if not text.strip():
        logger.warning("TTS called with empty text — skipping")
        return ("", 0)

    resolved_voice = voice or settings.tts_voice

    try:
        response = await _groq_tts_client.audio.speech.create(
            model=settings.tts_model,
            voice=resolved_voice,  # type: ignore[arg-type]
            input=text,
            response_format="wav",  # only format supported by Orpheus on Groq
        )
        wav_bytes = response.content
        speech_b64 = base64.b64encode(wav_bytes).decode()
        elapsed = int((time.monotonic() - start) * 1000)
        logger.info("TTS synthesis complete in %dms (%d bytes WAV)", elapsed, len(wav_bytes))
        return (speech_b64, elapsed)

    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.error("TTS synthesis error: %s", exc, exc_info=True)
        return ("", elapsed)


async def speak(
    text: str,
    language: str,
    voice_gender: str = "female",
    orpheus_voice: str | None = None,
) -> str:
    """Synthesise a spoken reply within the configured TTS time budget.

    Sahara is tried first for languages in ``SAHARA_VOICE_MAP`` (when
    ``settings.sahara_tts_enabled``), each provider capped at
    ``settings.tts_timeout_s``; Orpheus is the fallback and the primary for
    every other language.

    Args:
        text: Text to speak (the vision summary).
        language: Hover AI language code.
        voice_gender: ``"female"`` or ``"male"`` — passed to Sahara.
        orpheus_voice: Concrete Orpheus voice name for the fallback.

    Returns:
        Base64 WAV, or ``""`` if TTS is disabled or every provider failed.
    """
    if not settings.tts_enabled or not text.strip():
        return ""

    budget = settings.tts_timeout_s
    if settings.sahara_tts_enabled and language in SAHARA_VOICE_MAP:
        try:
            speech_b64, _ = await asyncio.wait_for(
                synthesise_sahara(text, language, voice_gender=voice_gender), budget
            )
        except TimeoutError:
            logger.warning("Sahara TTS exceeded %.1fs budget (language=%s)", budget, language)
            speech_b64 = ""
        if speech_b64:
            return speech_b64
        logger.warning("Sahara TTS failed for language=%s — falling back to Orpheus", language)

    try:
        speech_b64, _ = await asyncio.wait_for(synthesise(text, voice=orpheus_voice), budget)
    except TimeoutError:
        logger.warning("Orpheus TTS exceeded %.1fs budget", budget)
        return ""
    return speech_b64
