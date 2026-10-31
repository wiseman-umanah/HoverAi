"""Application configuration.

All environment variables are read exclusively from this module.
No other module should call os.getenv() directly — import `settings` instead.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings loaded from the .env file and environment variables.

    Attributes:
        database_url: Render PostgreSQL connection string.
        jwt_secret: Secret key used to sign JWT tokens. Must be >= 32 chars.
        jwt_algorithm: JWT signing algorithm (default HS256).
        jwt_expire_minutes: Token lifetime in minutes (default 15 minutes).
        intron_api_key: Intron hackathon access token — used as Bearer token.
        intron_stt_base_url: Base URL for the Intron Sahara API (TTS + streaming STT).
        groq_api_key: Groq API key for Whisper transcription and vision.
        vision_model: Groq model identifier for the vision LLM.
        whisper_model: Groq model identifier for Whisper transcription.
        vision_json_mode: Ask the vision model for ``response_format=json_object``.
            If the provider rejects ``response_format`` for the model, the call
            is retried once without it (and JSON mode is skipped for that model
            until restart); the tolerant text parser is always the fallback.
        vision_marked_max_edge: Long-edge pixel cap for screenshots that carry
            set-of-marks labels (requests with a screen context). Larger than
            the unmarked 768×432 so the drawn mark numbers stay legible.
        tts_timeout_s: Time budget (seconds) for each TTS provider attempt.
            Sahara gets one budget, the Orpheus fallback gets another, so the
            worst case is ~2x this value before the response ships without audio.
        port: Port the server binds to. Render (and most PaaS) inject ``PORT``.
        log_level: Root log level for app loggers (``LOG_LEVEL``).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str

    # ── Auth ──────────────────────────────────────────────────────────────────
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 15           # access token — 15 minutes
    jwt_refresh_expire_days: int = 90      # refresh token — 90 days

    # ── Intron (Sahara TTS + Sahara streaming STT) ────────────────────────────
    intron_api_key: str
    intron_stt_base_url: str = "https://infer.voice.intron.io"

    # ── Groq (Whisper transcription + vision) ─────────────────────────────────
    groq_api_key: str

    # ── Model names ───────────────────────────────────────────────────────────
    vision_model: str = "qwen/qwen3.8-27b"
    whisper_model: str = "whisper-large-v3"

    # ── Vision grounding (Phase 3) ────────────────────────────────────────────
    vision_json_mode: bool = True
    vision_marked_max_edge: int = 1280

    # ── TTS (Groq Orpheus — English) ──────────────────────────────────────────
    tts_model: str = "canopylabs/orpheus-v1-english"
    tts_voice: str = "autumn"  # valid: autumn | diana | hannah | austin | daniel | troy
    tts_enabled: bool = True   # set False to skip TTS and return empty speech_b64
    # Per-provider TTS budget (B10). Sahara's queue API can otherwise take ~60 s.
    tts_timeout_s: float = 8.0

    # ── Sahara TTS (Intron — African languages) ───────────────────────────────
    # When sahara_tts_enabled=True, African-language requests use Sahara TTS
    # (pcm+pidgin, yo+yoruba, ha+hausa, ig+igbo) for an authentic voice.
    # Groq Orpheus is used as fallback if Sahara fails, and for "en" / "fr".
    sahara_tts_enabled: bool = True

    # ── Sahara Streaming STT (Intron — real-time African-language STT) ────────
    # When sahara_stt_stream_enabled=True, the streaming WebSocket path is used
    # instead of Groq Whisper for African-language requests.
    # Requires the 'websockets' package and a Sahara account with streaming STT
    # access enabled.  Set False to keep using Groq Whisper (faster, always works).
    sahara_stt_stream_enabled: bool = False

    # ── Server ────────────────────────────────────────────────────────────────
    port: int = 8000           # Render sets PORT; the Docker CMD binds to it
    log_level: str = "INFO"


# Module-level singleton — import this everywhere instead of instantiating Settings()
settings = Settings()  # type: ignore[call-arg]  # fields are populated from .env at runtime
