"""Sahara streaming STT — real-time transcription via Intron WebSocket.

Uses the Intron Sahara streaming STT endpoint to transcribe audio in
real time as the user speaks, returning partial transcripts as they
arrive and a final committed transcript when the user stops.

This is an **alternative** to the Groq Whisper file-based path already
used in ``stt.py``.  It is NOT wired into the main query route by default
— it exists as a ready-to-use service so you can integrate it when
streaming STT is available on your Intron account tier.

Endpoint: ``wss://infer.voice.intron.io/stt/v1/stream``

WebSocket message protocol
──────────────────────────
Client → Server:
  {"message_type": "INPUT_AUDIO_CHUNK", "audio": "<base64 PCM16 LE>"}
  {"message_type": "COMMIT"}

Server → Client:
  {"message_type": "SESSION_CREATED",   "session_id": "...", "credit_balance": ...}
  {"message_type": "AUDIO_CHUNK_ACK",   ...}
  {"message_type": "PARTIAL_TRANSCRIPT","transcript": "..."}
  {"message_type": "COMMITTED_TRANSCRIPT","transcript": "..."}
  {"message_type": "INPUT_ERROR",       "message": "..."}
  {"message_type": "SESSION_ERROR",     "message": "..."}

Audio format required by Sahara
────────────────────────────────
  - Encoding: PCM 16-bit little-endian (s16le)
  - Sample rate: 16 000 Hz
  - Channels: mono (1)
  - Chunk size: 1 KB – 32 KB per message
  - The audio coming from the desktop client is WebM (Opus).
    Convert to PCM16 LE with ffmpeg before streaming:
      ffmpeg -i audio.webm -f s16le -ar 16000 -ac 1 -

Typical latency profile
────────────────────────
  - Session open: ~1-2 s
  - First PARTIAL_TRANSCRIPT: ~1 s after first chunk arrives
  - COMMITTED_TRANSCRIPT: ~1-2 s after COMMIT
  - Total time-to-transcript for a 3-5 s utterance: ~3-4 s

Compared to Groq Whisper (~1-2 s total), streaming Sahara is slower but
produces a better transcript for code-switched African languages.
"""

import asyncio
import base64
import io
import json
import logging
import time
import wave
from collections.abc import AsyncIterator

from app.config import settings
from app.services.audio import convert_to_wav
from app.services.stt import LANGUAGE_MAP

logger = logging.getLogger(__name__)

# Streaming endpoint
_STREAM_URL = "wss://infer.voice.intron.io/stt/v1/stream"

# Audio chunk size in bytes sent per WebSocket message.
# 3 200 bytes = 100 ms of PCM16 LE at 16 kHz mono (16 000 * 2 / 10).
# Staying well within the 1 KB – 32 KB per-chunk limit.
_CHUNK_BYTES: int = 3200

# How long to wait for COMMITTED_TRANSCRIPT after sending COMMIT.
_COMMIT_TIMEOUT_S: float = 15.0


async def _to_pcm16(audio_bytes: bytes) -> bytes:
    """Convert uploaded audio to raw PCM16 LE 16 kHz mono samples.

    Sahara streaming STT expects bare samples — no container, no header.
    v2 uploads are already 16 kHz mono WAV, so only the header is stripped;
    anything else goes through ``convert_to_wav`` (async ffmpeg with a
    timeout — the old synchronous ``subprocess.run`` blocked the event loop
    with no time limit, B22).

    Args:
        audio_bytes: Uploaded audio (WAV, or legacy WebM/Opus).

    Returns:
        Raw PCM16 LE bytes at 16 kHz mono.

    Raises:
        AudioConversionError: If the audio cannot be decoded.
    """
    wav_bytes = await convert_to_wav(audio_bytes)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.readframes(wf.getnframes())


async def stream_transcribe(
    webm_bytes: bytes,
    language: str,
    partial_callback: "AsyncIterator[str] | None" = None,
) -> tuple[str, int]:
    """Transcribe audio using Sahara streaming STT.

    Converts WebM to PCM16 LE, opens a WebSocket session to Sahara,
    streams the audio in 100 ms chunks, collects partial transcripts,
    and returns the final committed transcript.

    If ``partial_callback`` is provided it must be an async callable that
    accepts a single ``str`` argument — it will be called for each
    ``PARTIAL_TRANSCRIPT`` message so the caller can act on incremental
    results (e.g. display them in the UI before the final result).

    Falls back gracefully: any error returns ``("", elapsed_ms)`` so the
    caller can fall back to Groq Whisper without crashing.

    Args:
        webm_bytes: Raw WebM audio from the desktop MediaRecorder.
        language: Hover AI language code (e.g. ``"en-pidgin"``).
        partial_callback: Optional async callable called with each partial
            transcript string as it arrives.

    Returns:
        ``(transcript, elapsed_ms)`` — final committed transcript and
        total round-trip time in milliseconds.
        Returns ``("", elapsed_ms)`` on any error.
    """
    try:
        import websockets  # optional dep — only needed for streaming path
    except ImportError:
        logger.error(
            "Sahara streaming STT requires 'websockets' — "
            "run: pip install websockets"
        )
        return ("", 0)

    start = time.monotonic()
    sahara_lang = LANGUAGE_MAP.get(language, "en")

    # Step 1: convert to raw PCM16 LE
    try:
        pcm_bytes = await _to_pcm16(webm_bytes)
        logger.debug(
            "Sahara stream STT: converted %dB audio → %dB PCM16 LE",
            len(webm_bytes), len(pcm_bytes),
        )
    except Exception as exc:
        logger.error("Sahara stream STT: PCM conversion failed: %s", exc)
        return ("", int((time.monotonic() - start) * 1000))

    # Step 2: chunk the PCM into 100 ms pieces
    chunks = [
        pcm_bytes[i:i + _CHUNK_BYTES]
        for i in range(0, len(pcm_bytes), _CHUNK_BYTES)
    ]
    logger.debug(
        "Sahara stream STT: %d chunks of %dB each (%.1fs of audio)",
        len(chunks), _CHUNK_BYTES, len(pcm_bytes) / 32000,
    )

    url = (
        f"{_STREAM_URL}"
        f"?use_language_asr_input={sahara_lang}"
        f"&sample_rate=16000"
        f"&bit_rate=16"
        f"&num_channels=1"
    )
    headers = {"Authorization": f"Bearer {settings.intron_api_key}"}

    transcript = ""
    partials: list[str] = []

    try:
        async with websockets.connect(
            url,
            additional_headers=headers,
            open_timeout=15.0,
            max_size=2**22,  # 4 MB — matches test.py; default 1 MB can be hit on long audio
        ) as ws:
            # Wait for SESSION_CREATED
            raw = await asyncio.wait_for(ws.recv(), timeout=15.0)
            sess = json.loads(raw)
            if sess.get("message_type") != "SESSION_CREATED":
                logger.error(
                    "Sahara stream STT: unexpected first message: %s", sess
                )
                return ("", int((time.monotonic() - start) * 1000))

            logger.info(
                "Sahara stream STT: session created — lang=%s credit=%.2f",
                sahara_lang,
                sess.get("credit_balance", 0),
            )

            # Stream all chunks, collecting any partial transcripts
            for i, chunk in enumerate(chunks):
                # Pad to 1 KB minimum (API requirement)
                if len(chunk) < 1024:
                    chunk = chunk.ljust(1024, b"\x00")
                b64 = base64.b64encode(chunk).decode()
                await ws.send(json.dumps({
                    "message_type": "INPUT_AUDIO_CHUNK",
                    "audio_base_64": b64,
                    "ack_id": i,
                }))
                # Drain any immediately available messages (partials / acks)
                try:
                    msg_raw = await asyncio.wait_for(ws.recv(), timeout=0.05)
                    msg = json.loads(msg_raw)
                    mtype = msg.get("message_type", "")
                    if mtype == "PARTIAL_TRANSCRIPT":
                        partial = msg.get("transcript", "")
                        partials.append(partial)
                        logger.debug(
                            "Sahara stream STT partial [chunk %d]: %r", i, partial
                        )
                        if partial_callback is not None:
                            await partial_callback(partial)  # type: ignore[operator]
                    elif mtype == "AUDIO_CHUNK_ACK":
                        pass  # expected — ignore
                    elif "ERROR" in mtype:
                        logger.error(
                            "Sahara stream STT INPUT_ERROR at chunk %d: %s",
                            i, msg.get("message", ""),
                        )
                        return ("", int((time.monotonic() - start) * 1000))
                except asyncio.TimeoutError:
                    pass  # no message yet — keep streaming

            # Send COMMIT and wait for the final transcript
            await ws.send(json.dumps({"message_type": "COMMIT"}))
            logger.debug("Sahara stream STT: COMMIT sent")

            deadline = time.monotonic() + _COMMIT_TIMEOUT_S
            while time.monotonic() < deadline:
                try:
                    msg_raw = await asyncio.wait_for(
                        ws.recv(), timeout=_COMMIT_TIMEOUT_S
                    )
                except asyncio.TimeoutError:
                    break

                msg = json.loads(msg_raw)
                mtype = msg.get("message_type", "")

                if mtype == "PARTIAL_TRANSCRIPT":
                    partial = msg.get("transcript", "")
                    partials.append(partial)
                    if partial_callback is not None:
                        await partial_callback(partial)  # type: ignore[operator]

                elif mtype == "COMMITTED_TRANSCRIPT":
                    # API returns the final text in "transcript_text"
                    transcript = msg.get("transcript_text") or msg.get("transcript", "")
                    elapsed = int((time.monotonic() - start) * 1000)
                    logger.info(
                        "Sahara stream STT committed in %dms — lang=%s: %r",
                        elapsed, sahara_lang, transcript[:80],
                    )
                    return (transcript, elapsed)

                elif "ERROR" in mtype:
                    logger.error(
                        "Sahara stream STT error after COMMIT: %s — %s",
                        mtype, msg.get("message", ""),
                    )
                    break

            # COMMIT timed out — use the last partial as best-effort
            if partials:
                transcript = partials[-1]
                logger.warning(
                    "Sahara stream STT: COMMIT timed out — using last partial: %r",
                    transcript[:80],
                )
            else:
                logger.error(
                    "Sahara stream STT: COMMIT timed out with no partials"
                )

    except Exception as exc:
        logger.error("Sahara stream STT error: %s", exc, exc_info=True)

    elapsed = int((time.monotonic() - start) * 1000)
    return (transcript, elapsed)
