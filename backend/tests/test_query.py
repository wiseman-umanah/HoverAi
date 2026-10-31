"""Tests for POST /api/v1/query and the agent routes' upload handling.

All providers (STT, vision, TTS, ffmpeg) are monkeypatched — nothing here
talks to Groq, Intron or spawns ffmpeg for the legacy path.
"""

import base64
import io
import json
import wave

import httpx
import pytest
from PIL import Image

from app.config import settings
from app.routers import common as common_router
from app.routers import query as query_router
from app.schemas.vision import BeaconStep, ScreenContext, StepTarget, VisionResult
from app.services import audio as audio_service
from app.services import stt as stt_service
from app.services import tts as tts_service
from app.services.vision import VisionParseError
from app.uploads import MAX_AUDIO_BYTES, MAX_SCREENSHOT_BYTES

_VISION_RESULT = VisionResult(
    summary="I can see Excel — here's how to sum that column.",
    steps=[BeaconStep(step=1, instruction="Click cell B10.", keys=None, tip=None)],
)


def _wav_16k_mono(duration_ms: int = 200) -> bytes:
    """Build a 16 kHz mono PCM16 WAV like the v2 desktop client sends.

    Args:
        duration_ms: Length of the silent clip.

    Returns:
        WAV bytes.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * (16 * duration_ms))
    return buf.getvalue()


def _image(fmt: str = "JPEG", size: tuple[int, int] = (1600, 900)) -> bytes:
    """Build an in-memory screenshot image.

    Args:
        fmt: PIL format name (``"JPEG"`` or ``"PNG"``).
        size: Image dimensions.

    Returns:
        Encoded image bytes.
    """
    buf = io.BytesIO()
    Image.new("RGB", size, (30, 120, 200)).save(buf, format=fmt)
    return buf.getvalue()


class _Calls:
    """Records what the fake providers received."""

    def __init__(self) -> None:
        self.whisper_wav: bytes | None = None
        self.vision_compressed: str | None = None
        self.vision_context: object = "not called"
        self.tts_text: str | None = None


@pytest.fixture()
def providers(monkeypatch) -> _Calls:
    """Replace Whisper, vision and TTS with in-process fakes.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Returns:
        A ``_Calls`` recorder.
    """
    calls = _Calls()

    async def fake_whisper(wav_bytes: bytes, language: str) -> tuple[str, int]:
        calls.whisper_wav = wav_bytes
        return ("sum this column", 42)

    async def fake_vision(screenshot, transcript, language, compressed_b64=None, **kwargs):
        calls.vision_compressed = compressed_b64
        calls.vision_context = kwargs.get("context")
        return _VISION_RESULT

    async def fake_speak(text, language, voice_gender="female", orpheus_voice=None):
        calls.tts_text = text
        return base64.b64encode(b"RIFF-fake-wav").decode()

    monkeypatch.setattr(stt_service, "_call_groq_whisper", fake_whisper)
    monkeypatch.setattr(query_router, "run_vision", fake_vision)
    monkeypatch.setattr(query_router, "speak", fake_speak)
    return calls


async def _post_query(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    *,
    audio: tuple[str, bytes, str] | None = None,
    screenshot_file: bytes | None = None,
    screenshot_text: str | None = None,
    context: str | None = None,
) -> httpx.Response:
    """POST a multipart /query request.

    Args:
        client: Test client.
        headers: Auth headers.
        audio: ``(filename, bytes, content_type)`` for the audio part.
        screenshot_file: JPEG bytes sent as a file part (v2 client).
        screenshot_text: Base64 string sent as a text field (v1 client).
        context: Optional ``context`` JSON text field (Phase 3 client).

    Returns:
        The HTTP response.
    """
    files: dict[str, tuple[str, bytes, str]] = {
        "audio": audio or ("audio.wav", _wav_16k_mono(), "audio/wav")
    }
    data: dict[str, str] = {"language": "en"}
    if screenshot_file is not None:
        files["screenshot"] = ("screen.jpg", screenshot_file, "image/jpeg")
    if screenshot_text is not None:
        data["screenshot"] = screenshot_text
    if context is not None:
        data["context"] = context
    return await client.post("/api/v1/query", headers=headers, files=files, data=data)


class TestQueryHappyPath:
    """The v2 contract and the legacy v1 contract both work end to end."""

    async def test_wav_and_jpeg_file_parts(self, client, auth_headers, providers):
        """v2 client: WAV audio + JPEG screenshot file part → full response."""
        wav = _wav_16k_mono()
        resp = await _post_query(
            client, auth_headers,
            audio=("audio.wav", wav, "audio/wav"),
            screenshot_file=_image("JPEG"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["transcript"] == "sum this column"
        assert body["summary"] == _VISION_RESULT.summary
        assert body["steps"] == [
            {"step": 1, "instruction": "Click cell B10.", "keys": None, "tip": None, "target": None}
        ]
        assert base64.b64decode(body["speech_b64"]) == b"RIFF-fake-wav"
        assert body["conversation_id"]
        # Target-format WAV goes to Whisper untouched; screenshot was recompressed.
        assert providers.whisper_wav == wav
        compressed = Image.open(io.BytesIO(base64.b64decode(providers.vision_compressed or "")))
        assert compressed.format == "JPEG"
        assert compressed.width <= 768 and compressed.height <= 432

    async def test_legacy_webm_and_base64_png_text_field(
        self, client, auth_headers, providers, monkeypatch
    ):
        """v1 client: WebM audio (via mocked ffmpeg) + base64 PNG data: URI field."""
        converted = _wav_16k_mono(300)
        seen: list[bytes] = []

        async def fake_ffmpeg(audio_bytes: bytes) -> bytes:
            seen.append(audio_bytes)
            with wave.open(io.BytesIO(converted), "rb") as wf:
                return wf.readframes(wf.getnframes())

        monkeypatch.setattr(audio_service, "_ffmpeg_to_pcm", fake_ffmpeg)
        webm = b"\x1aE\xdf\xa3" + b"\x00" * 64
        png_b64 = "data:image/png;base64," + base64.b64encode(_image("PNG")).decode()

        resp = await _post_query(
            client, auth_headers,
            audio=("audio.webm", webm, "audio/webm"),
            screenshot_text=png_b64,
        )
        assert resp.status_code == 200, resp.text
        assert seen == [webm]
        assert providers.whisper_wav == converted
        assert resp.json()["transcript"] == "sum this column"

    async def test_tts_failure_still_returns_steps(
        self, client, auth_headers, providers, monkeypatch
    ):
        """speech_b64 is empty when TTS fails; the request still succeeds."""

        async def no_speech(*args, **kwargs):
            return ""

        monkeypatch.setattr(query_router, "speak", no_speech)
        resp = await _post_query(client, auth_headers, screenshot_file=_image())
        assert resp.status_code == 200
        assert resp.json()["speech_b64"] == ""
        assert resp.json()["steps"]


_CONTEXT = {
    "app": "Code",
    "window_title": "main.rs — HoverAi",
    "window": {"x": 0.0, "y": 0.03, "w": 1.0, "h": 0.97},
    "cursor": {"x": 0.41, "y": 0.62},
    "elements": [
        {"id": 1, "role": "button", "name": "Save", "x": 0.10, "y": 0.20,
         "w": 0.05, "h": 0.03, "enabled": True},
    ],
}


class TestQueryScreenContext:
    """Phase 3: the optional ``context`` field and step targets."""

    async def test_valid_context_reaches_vision(self, client, auth_headers, providers):
        """A valid context is parsed and the marked screenshot kept larger."""
        resp = await _post_query(
            client, auth_headers, screenshot_file=_image(), context=json.dumps(_CONTEXT)
        )
        assert resp.status_code == 200, resp.text
        ctx = providers.vision_context
        assert isinstance(ctx, ScreenContext)
        assert ctx.app == "Code" and [e.id for e in ctx.elements] == [1]
        compressed = Image.open(io.BytesIO(base64.b64decode(providers.vision_compressed or "")))
        assert max(compressed.size) == settings.vision_marked_max_edge

    async def test_malformed_context_is_ignored(self, client, auth_headers, providers):
        """Broken JSON in ``context`` doesn't fail the request."""
        resp = await _post_query(
            client, auth_headers, screenshot_file=_image(), context="{not json"
        )
        assert resp.status_code == 200, resp.text
        assert providers.vision_context is None
        compressed = Image.open(io.BytesIO(base64.b64decode(providers.vision_compressed or "")))
        assert compressed.width <= 768 and compressed.height <= 432

    async def test_absent_context(self, client, auth_headers, providers):
        """Old clients: no ``context`` field → vision gets None."""
        resp = await _post_query(client, auth_headers, screenshot_file=_image())
        assert resp.status_code == 200, resp.text
        assert providers.vision_context is None

    async def test_targets_in_response(self, client, auth_headers, providers, monkeypatch):
        """Resolved step targets are serialised in the response."""
        result = VisionResult(
            summary="Save it.",
            steps=[
                BeaconStep(
                    step=1, instruction="Click Save.",
                    target=StepTarget(element_id=1, x=0.1, y=0.2, w=0.05, h=0.03),
                ),
                BeaconStep(step=2, instruction="Press Enter.", keys="Enter"),
            ],
            usage={"prompt_tokens": 1},
        )

        async def grounded_vision(*args, **kwargs):
            return result

        monkeypatch.setattr(query_router, "run_vision", grounded_vision)
        resp = await _post_query(
            client, auth_headers, screenshot_file=_image(), context=json.dumps(_CONTEXT)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["steps"][0]["target"] == {
            "element_id": 1, "x": 0.1, "y": 0.2, "w": 0.05, "h": 0.03
        }
        assert body["steps"][1]["target"] is None
        assert "usage" not in body


class TestQuerySizeCaps:
    """B12: oversized uploads are refused with 413 and a readable detail."""

    async def test_audio_over_10mb_returns_413(self, client, auth_headers, providers):
        """Audio larger than MAX_AUDIO_BYTES → 413."""
        big = b"RIFF" + b"\x00" * MAX_AUDIO_BYTES
        resp = await _post_query(
            client, auth_headers,
            audio=("audio.wav", big, "audio/wav"),
            screenshot_file=_image(),
        )
        assert resp.status_code == 413
        assert "Audio recording is too large" in resp.json()["detail"]

    async def test_screenshot_over_8mb_returns_413(self, client, auth_headers, providers):
        """Screenshot file larger than MAX_SCREENSHOT_BYTES → 413."""
        resp = await _post_query(
            client, auth_headers, screenshot_file=b"\xff" * (MAX_SCREENSHOT_BYTES + 1)
        )
        assert resp.status_code == 413
        assert "Screenshot is too large" in resp.json()["detail"]

    async def test_large_jpeg_file_part_over_1mb_is_accepted(
        self, client, auth_headers, providers
    ):
        """B1: a file part bigger than Starlette's 1 MB text-field cap works."""
        noisy = Image.effect_noise((2400, 1350), 100).convert("RGB")
        buf = io.BytesIO()
        noisy.save(buf, format="JPEG", quality=95)
        assert len(buf.getvalue()) > 1024 * 1024
        resp = await _post_query(client, auth_headers, screenshot_file=buf.getvalue())
        assert resp.status_code == 200, resp.text


class TestQueryErrorMapping:
    """B7: each failure class gets its own status and human-readable detail."""

    async def test_undecodable_audio_returns_400(
        self, client, auth_headers, providers, monkeypatch
    ):
        """ffmpeg failure → 400 'Audio could not be decoded.'."""

        async def broken_ffmpeg(audio_bytes: bytes) -> bytes:
            raise audio_service.AudioConversionError("ffmpeg exited with 1")

        monkeypatch.setattr(audio_service, "_ffmpeg_to_pcm", broken_ffmpeg)
        resp = await _post_query(
            client, auth_headers,
            audio=("audio.webm", b"not audio", "audio/webm"),
            screenshot_file=_image(),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == common_router.AUDIO_UNDECODABLE_DETAIL

    async def test_empty_audio_returns_400(self, client, auth_headers, providers):
        """A zero-byte audio part → 400."""
        resp = await _post_query(
            client, auth_headers,
            audio=("audio.wav", b"", "audio/wav"),
            screenshot_file=_image(),
        )
        assert resp.status_code == 400

    async def test_undecodable_screenshot_returns_400(self, client, auth_headers, providers):
        """Bytes that aren't an image → 400 'Screenshot could not be decoded.'."""
        resp = await _post_query(client, auth_headers, screenshot_file=b"not an image")
        assert resp.status_code == 400
        assert resp.json()["detail"] == common_router.SCREENSHOT_UNDECODABLE_DETAIL

    async def test_empty_transcript_returns_422(
        self, client, auth_headers, providers, monkeypatch
    ):
        """Provider succeeded but heard nothing → 422 with a friendly message."""

        async def silent(wav_bytes: bytes, language: str) -> tuple[str, int]:
            return ("", 10)

        monkeypatch.setattr(stt_service, "_call_groq_whisper", silent)
        resp = await _post_query(client, auth_headers, screenshot_file=_image())
        assert resp.status_code == 422
        assert resp.json()["detail"] == "I didn't catch that — please try again."

    async def test_stt_provider_failure_returns_502(
        self, client, auth_headers, providers, monkeypatch
    ):
        """Whisper error/timeout → 502."""

        async def down(wav_bytes: bytes, language: str) -> tuple[str, int]:
            raise stt_service.STTProviderError("connection reset")

        monkeypatch.setattr(stt_service, "_call_groq_whisper", down)
        resp = await _post_query(client, auth_headers, screenshot_file=_image())
        assert resp.status_code == 502
        assert resp.json()["detail"] == common_router.STT_UNAVAILABLE_DETAIL

    async def test_stt_rate_limit_returns_429(
        self, client, auth_headers, providers, monkeypatch
    ):
        """Whisper 429 → 429."""

        async def limited(wav_bytes: bytes, language: str) -> tuple[str, int]:
            raise stt_service.STTProviderError("429", rate_limited=True)

        monkeypatch.setattr(stt_service, "_call_groq_whisper", limited)
        resp = await _post_query(client, auth_headers, screenshot_file=_image())
        assert resp.status_code == 429

    async def test_vision_failure_returns_502(
        self, client, auth_headers, providers, monkeypatch
    ):
        """Unparseable vision output → 502."""

        async def bad_vision(*args, **kwargs):
            raise VisionParseError("not json")

        monkeypatch.setattr(query_router, "run_vision", bad_vision)
        resp = await _post_query(client, auth_headers, screenshot_file=_image())
        assert resp.status_code == 502
        assert resp.json()["detail"]

    async def test_missing_auth_returns_401(self, client, providers):
        """No bearer token → 401 before any upload is processed."""
        resp = await _post_query(client, {}, screenshot_file=_image())
        assert resp.status_code == 401


class TestRealWhisperErrorTranslation:
    """``_call_groq_whisper`` raises typed errors instead of returning ""."""

    async def test_rate_limit_error_is_flagged(self, monkeypatch):
        """openai.RateLimitError → STTProviderError(rate_limited=True)."""
        import openai

        async def raise_429(**kwargs):
            raise openai.RateLimitError(
                "slow down",
                response=httpx.Response(429, request=httpx.Request("POST", "http://x")),
                body=None,
            )

        monkeypatch.setattr(
            stt_service._groq_client.audio.transcriptions, "create", raise_429
        )
        with pytest.raises(stt_service.STTProviderError) as excinfo:
            await stt_service._call_groq_whisper(_wav_16k_mono(), "en")
        assert excinfo.value.rate_limited is True

    async def test_generic_error_is_not_rate_limited(self, monkeypatch):
        """Any other failure → STTProviderError(rate_limited=False)."""

        async def boom(**kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(stt_service._groq_client.audio.transcriptions, "create", boom)
        with pytest.raises(stt_service.STTProviderError) as excinfo:
            await stt_service._call_groq_whisper(_wav_16k_mono(), "en")
        assert excinfo.value.rate_limited is False


class TestTTSBudget:
    """B10: TTS is time-boxed per provider and degrades to empty audio."""

    async def test_slow_sahara_falls_back_to_orpheus(self, monkeypatch):
        """Sahara exceeding the budget → Orpheus result is used."""
        import asyncio

        async def slow_sahara(*args, **kwargs):
            await asyncio.sleep(5)
            return ("sahara", 0)

        async def orpheus(text, voice=None):
            return ("orpheus", 0)

        monkeypatch.setattr(tts_service, "synthesise_sahara", slow_sahara)
        monkeypatch.setattr(tts_service, "synthesise", orpheus)
        monkeypatch.setattr(tts_service.settings, "tts_timeout_s", 0.05)
        assert await tts_service.speak("hello", "en-pidgin") == "orpheus"

    async def test_everything_slow_returns_empty(self, monkeypatch):
        """Both providers over budget → ''."""
        import asyncio

        async def slow(*args, **kwargs):
            await asyncio.sleep(5)
            return ("late", 0)

        monkeypatch.setattr(tts_service, "synthesise_sahara", slow)
        monkeypatch.setattr(tts_service, "synthesise", slow)
        monkeypatch.setattr(tts_service.settings, "tts_timeout_s", 0.05)
        assert await tts_service.speak("hello", "yo") == ""


class TestAgentUploads:
    """/agent/start and /agent/turn accept the same screenshot/audio formats."""

    async def test_agent_start_with_jpeg_file_part(
        self, client, auth_headers, providers, monkeypatch
    ):
        """Screenshot file part + WAV reach agent_start as raw bytes."""
        from app.routers import agent as agent_router
        from app.schemas.agent import AgentResponse

        received: dict[str, object] = {}

        async def fake_agent_start(transcript, screenshot, language, tts_voice):
            received.update(transcript=transcript, screenshot=screenshot)
            return AgentResponse(
                session_id="s1", action=None, summary="done", speech_b64="", done=True, turn=1
            )

        monkeypatch.setattr(agent_router, "agent_start", fake_agent_start)
        jpeg = _image()
        resp = await client.post(
            "/api/v1/agent/start",
            headers=auth_headers,
            files={
                "audio": ("audio.wav", _wav_16k_mono(), "audio/wav"),
                "screenshot": ("screen.jpg", jpeg, "image/jpeg"),
            },
        )
        assert resp.status_code == 200, resp.text
        assert received == {"transcript": "sum this column", "screenshot": jpeg}

    async def test_agent_turn_with_legacy_base64_and_unknown_session(
        self, client, auth_headers, providers
    ):
        """Legacy base64 field is accepted; an unknown session → 404."""
        resp = await client.post(
            "/api/v1/agent/turn",
            headers=auth_headers,
            data={
                "session_id": "does-not-exist",
                "screenshot": base64.b64encode(_image("PNG", (320, 180))).decode(),
            },
        )
        assert resp.status_code == 404
