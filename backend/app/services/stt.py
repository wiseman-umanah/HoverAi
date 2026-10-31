"""Speech-to-text pipeline.

Default engine: Groq Whisper (``whisper-large-v3``).
Fast (~1-2 s), supports English and major African languages via auto-detection.

Optional Sahara streaming mode (``sahara_stt_stream_enabled=True``):
  Uses the Intron Sahara WebSocket streaming STT for African-language requests.
  Falls back to Groq Whisper automatically if the stream returns empty.

Language code mapping — confirmed against Sahara /stt/v1/languages endpoint:

  Hover AI code  |  Sahara STT  |  Language
  ───────────────────────────────────────────────────────
  en             |  en          |  English
  en-pidgin      |  pcm         |  Nigerian Pidgin Creole
  yo             |  yo          |  Yoruba
  ha             |  ha          |  Hausa
  ig             |  ig          |  Igbo
  af             |  af          |  Afrikaans
  am             |  am          |  Amharic
  rw             |  rw          |  Kinyarwanda
  lg             |  lg          |  Luganda
  om             |  om          |  Oromo
  sn             |  sn          |  Shona
  sw             |  sw          |  Swahili
  wo             |  wo          |  Wolof
  zu             |  zu          |  Zulu (STT only — TTS uses en+zulu accent)

Error contract (B7):
  A provider failure raises ``STTProviderError`` (``rate_limited=True`` for a
  429) instead of returning an empty transcript, so the route can tell
  "Groq is down" (502/429) apart from "the user said nothing" (422).
"""

import logging
import time

import openai
from openai import AsyncOpenAI

from app.config import settings
from app.schemas.stt import STTResult

logger = logging.getLogger(__name__)

# Maps Hover AI language codes → Intron Sahara STT language codes.
# Confirmed against the Sahara /stt/v1/languages endpoint.
LANGUAGE_MAP: dict[str, str] = {
    "en":        "en",   # English
    "en-pidgin": "pcm",  # Nigerian Pidgin Creole
    "yo":        "yo",   # Yoruba
    "ha":        "ha",   # Hausa
    "ig":        "ig",   # Igbo
    "af":        "af",   # Afrikaans
    "am":        "am",   # Amharic
    "rw":        "rw",   # Kinyarwanda
    "lg":        "lg",   # Luganda
    "om":        "om",   # Oromo
    "sn":        "sn",   # Shona
    "sw":        "sw",   # Swahili
    "wo":        "wo",   # Wolof
    "zu":        "zu",   # Zulu — STT only; TTS falls back to en+zulu accent
}

# Groq OpenAI-compatible client — shared across requests (thread-safe)
# max_retries=1 + timeout=15s: Whisper is fast; cap retries to stay within
# ngrok's 30s free-tier window.
_groq_client = AsyncOpenAI(
    api_key=settings.groq_api_key,
    base_url="https://api.groq.com/openai/v1",
    max_retries=1,
    timeout=15.0,
)


class STTProviderError(Exception):
    """Raised when the speech-to-text provider fails or times out.

    Attributes:
        rate_limited: True when the provider answered 429 (rate limit).
    """

    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        """Initialise with a description and the rate-limit flag.

        Args:
            message: Description of the provider failure (for logs).
            rate_limited: Whether the failure was a 429 from the provider.
        """
        super().__init__(message)
        self.rate_limited = rate_limited


def _sahara_language(hover_lang: str) -> str:
    """Map a Hover AI language code to the Intron Sahara language code.

    Falls back to ``"en"`` for any unrecognised code so the request still
    proceeds rather than failing hard.

    Args:
        hover_lang: Language code used by the Hover AI front-end.

    Returns:
        The corresponding Intron Sahara language code string.
    """
    return LANGUAGE_MAP.get(hover_lang, "en")


# ISO 639-1 codes that Groq Whisper explicitly supports.
# Codes NOT in this set are omitted from the API call — Whisper auto-detects
# them more accurately without a potentially wrong hint.
# Confirmed supported: af, am, sw are valid Whisper language codes.
# rw, lg, om, sn, wo, zu, pcm are not — omit them (auto-detect works better).
_GROQ_WHISPER_SUPPORTED: frozenset[str] = frozenset([
    "en", "fr", "de", "es", "pt", "it", "nl", "pl", "ru", "zh",
    "ja", "ko", "ar", "hi", "bn", "ur", "fa", "tr", "vi", "th",
    "id", "ms", "yo", "ha", "af", "am", "sw",
])


async def _call_groq_whisper(wav_bytes: bytes, language: str) -> tuple[str, int]:
    """Transcribe audio using Groq Whisper.

    Uses Groq's OpenAI-compatible API endpoint. The audio must be in WAV
    format — convert from WebM first using ``audio.convert_to_wav()``.

    The ``language`` hint is only passed when it is a code Whisper supports.
    Unsupported African codes (``ig``, ``pcm``) are omitted so Whisper
    auto-detects the language and avoids a 400 ``invalid_language`` error.

    Args:
        wav_bytes: WAV audio bytes at 16 kHz mono (output of convert_to_wav).
        language: Hover AI language code (e.g. ``"ig"``, ``"en-pidgin"``).

    Returns:
        A tuple of ``(transcript, elapsed_ms)``. The transcript may be empty
        when the provider succeeded but heard no speech.

    Raises:
        STTProviderError: On any provider/network failure or timeout.
    """
    start = time.monotonic()

    # Map to ISO 639-1 then check support — unsupported → None (auto-detect)
    _iso = LANGUAGE_MAP.get(language, "en")
    whisper_lang: str | None = _iso if _iso in _GROQ_WHISPER_SUPPORTED else None

    try:
        if whisper_lang is not None:
            response = await _groq_client.audio.transcriptions.create(
                model=settings.whisper_model,
                file=("audio.wav", wav_bytes, "audio/wav"),
                language=whisper_lang,
            )
        else:
            # Omit language entirely — Whisper auto-detects unsupported codes
            response = await _groq_client.audio.transcriptions.create(
                model=settings.whisper_model,
                file=("audio.wav", wav_bytes, "audio/wav"),
            )
        elapsed = int((time.monotonic() - start) * 1000)
    except openai.RateLimitError as exc:
        logger.warning("Groq Whisper rate-limited: %s", exc)
        raise STTProviderError("Groq Whisper rate limit", rate_limited=True) from exc
    except Exception as exc:
        logger.error("Groq Whisper STT error: %s", exc, exc_info=True)
        raise STTProviderError(f"Groq Whisper failed: {exc}") from exc

    elapsed = int((time.monotonic() - start) * 1000)
    transcript = response.text if hasattr(response, "text") else str(response)
    logger.info("Groq Whisper transcription complete in %dms", elapsed)
    return (transcript.strip(), elapsed)


async def run_stt(audio_bytes: bytes, wav_bytes: bytes, language: str) -> STTResult:
    """Transcribe audio using Groq Whisper, or Sahara streaming if enabled.

    **Default mode** (``sahara_stt_stream_enabled=False``):
      Groq Whisper only — fast (~1-2s), no Sahara upload or polling.

    **Sahara streaming mode** (``sahara_stt_stream_enabled=True``):
      Sahara WebSocket streaming STT for African-language requests
      (en-pidgin, yo, ha, ig) with automatic Groq Whisper fallback.
      Requires ``websockets`` and streaming STT access on the Intron account.

    Args:
        audio_bytes: Raw WebM bytes — used by Sahara streaming if enabled.
        wav_bytes: WAV bytes at 16 kHz mono — used by Groq Whisper.
        language: Hover AI language code (e.g. ``"en-pidgin"``).

    Returns:
        An ``STTResult`` with the transcript ready for immediate use. The
        transcript is empty only when the provider succeeded and heard nothing.

    Raises:
        STTProviderError: If Groq Whisper fails (see ``_call_groq_whisper``).
    """
    # Sahara streaming path — only when explicitly enabled
    _SAHARA_STREAM_LANGS = {"en-pidgin", "yo", "ha", "ig"}
    if settings.sahara_stt_stream_enabled and language in _SAHARA_STREAM_LANGS:
        from app.services.sahara_stt_stream import stream_transcribe  # noqa: PLC0415
        stream_text, stream_ms = await stream_transcribe(audio_bytes, language)
        if stream_text:
            logger.info("Sahara stream STT succeeded (%dms)", stream_ms)
            return STTResult(transcript=stream_text, whisper_latency_ms=stream_ms)
        # Streaming returned empty — fall back to Groq Whisper
        logger.warning(
            "Sahara stream STT returned empty for language=%s — falling back to Groq Whisper",
            language,
        )

    # Default: Groq Whisper only
    whisper_text, whisper_ms = await _call_groq_whisper(wav_bytes, language)
    return STTResult(transcript=whisper_text, whisper_latency_ms=whisper_ms)
