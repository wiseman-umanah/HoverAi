"""Audio normalisation service.

Produces WAV 16 kHz mono 16-bit PCM — the format sent to Groq Whisper.
Intron Sahara streaming accepts the original bytes directly.

Design decisions:
  - The v2 desktop client records and sends exactly this format
    (``audio/wav``, 16 kHz mono PCM16), so the common path is a header check
    with Python's stdlib ``wave`` module and a zero-copy passthrough.
  - Anything else (legacy v1 clients send WebM/Opus; odd WAV variants such as
    float or 44.1 kHz stereo) is converted by an ``ffmpeg`` subprocess run via
    ``asyncio.create_subprocess_exec`` so the event loop is never blocked
    (B4), with a hard timeout. pydub was dropped: it imports ``audioop``
    (removed in Python 3.13) and ran ffmpeg synchronously.
  - ffmpeg writes raw ``s16le`` PCM to stdout and the WAV header is built
    here with ``wave``. A WAV muxed straight to a pipe has placeholder size
    fields (ffmpeg cannot seek back to patch them), which some readers reject.
  - Every failure — empty input, undecodable bytes, missing ffmpeg, timeout —
    raises ``AudioConversionError``; the routes map it to HTTP 400.
"""

import asyncio
import io
import logging
import wave

logger = logging.getLogger(__name__)

# Target format expected by Groq Whisper
_TARGET_SAMPLE_RATE = 16_000
_TARGET_CHANNELS = 1
_TARGET_SAMPLE_WIDTH = 2  # 16-bit PCM

# Hard cap on one ffmpeg run. A 10 MB WebM decodes in well under a second;
# anything slower than this is a pathological input.
FFMPEG_TIMEOUT_S: float = 20.0


class AudioConversionError(Exception):
    """Raised when the input audio cannot be decoded or resampled.

    Attributes:
        message: Human-readable description of what went wrong (for logs —
            the client gets a generic "Audio could not be decoded").
    """

    def __init__(self, message: str) -> None:
        """Initialise with a descriptive error message.

        Args:
            message: Description of the conversion failure.
        """
        super().__init__(message)
        self.message = message


def is_wav(audio_bytes: bytes) -> bool:
    """Return True if the bytes start with a RIFF/WAVE header.

    Args:
        audio_bytes: Raw uploaded audio.

    Returns:
        Whether the container is WAV (the codec inside may still be anything).
    """
    return len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE"


def _is_target_wav(audio_bytes: bytes) -> bool:
    """Check whether WAV bytes are already 16 kHz mono PCM16 with audio frames.

    Args:
        audio_bytes: Bytes with a RIFF/WAVE header.

    Returns:
        True if the file can be passed to Whisper unchanged.

    Raises:
        AudioConversionError: If the header is valid PCM but contains no frames.
    """
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            params = wf.getparams()
    except (wave.Error, EOFError):
        # Not plain PCM (e.g. IEEE float / exotic WAVE_FORMAT) — let ffmpeg try.
        return False
    if params.nframes == 0:
        raise AudioConversionError("WAV upload contains no audio frames")
    return (
        params.framerate == _TARGET_SAMPLE_RATE
        and params.nchannels == _TARGET_CHANNELS
        and params.sampwidth == _TARGET_SAMPLE_WIDTH
    )


def _pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw s16le mono 16 kHz PCM in a WAV container.

    Args:
        pcm: Raw little-endian 16-bit mono samples at 16 kHz.

    Returns:
        A complete WAV file.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(_TARGET_CHANNELS)
        wf.setsampwidth(_TARGET_SAMPLE_WIDTH)
        wf.setframerate(_TARGET_SAMPLE_RATE)
        wf.writeframes(pcm)
    return buf.getvalue()


async def _ffmpeg_to_pcm(audio_bytes: bytes) -> bytes:
    """Decode any ffmpeg-supported audio to raw 16 kHz mono s16le PCM.

    Args:
        audio_bytes: Input audio in any container/codec ffmpeg can probe.

    Returns:
        Raw PCM bytes.

    Raises:
        AudioConversionError: If ffmpeg is missing, fails, times out, or
            produces no samples.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", "pipe:0",
            "-vn", "-ar", str(_TARGET_SAMPLE_RATE), "-ac", str(_TARGET_CHANNELS),
            "-f", "s16le", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise AudioConversionError(
            "ffmpeg is not installed — cannot convert non-WAV audio"
        ) from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(audio_bytes), timeout=FFMPEG_TIMEOUT_S
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise AudioConversionError(
            f"ffmpeg timed out after {FFMPEG_TIMEOUT_S:.0f}s"
        ) from exc

    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip()[-300:]
        raise AudioConversionError(f"ffmpeg exited with {proc.returncode}: {detail}")
    if not stdout:
        raise AudioConversionError("ffmpeg produced no audio samples")
    return stdout


async def convert_to_wav(audio_bytes: bytes) -> bytes:
    """Normalise uploaded audio to WAV 16 kHz mono 16-bit PCM.

    WAV that is already in the target format is returned unchanged; every
    other input (WebM/Opus, OGG, MP3, FLAC, non-16 kHz WAV, …) is decoded by
    ffmpeg in a subprocess.

    Args:
        audio_bytes: Raw uploaded audio bytes.

    Returns:
        WAV bytes at 16 000 Hz, mono, 16-bit PCM — ready for Whisper.

    Raises:
        AudioConversionError: If the input is empty or cannot be decoded.
    """
    if not audio_bytes:
        raise AudioConversionError("Audio upload is empty")

    if is_wav(audio_bytes) and _is_target_wav(audio_bytes):
        return audio_bytes

    pcm = await _ffmpeg_to_pcm(audio_bytes)
    wav_bytes = _pcm_to_wav(pcm)
    logger.debug(
        "Audio converted: %d bytes → %d bytes WAV (16kHz mono)",
        len(audio_bytes),
        len(wav_bytes),
    )
    return wav_bytes
