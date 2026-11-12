"""Tests for the audio normalisation service (WAV passthrough + async ffmpeg)."""

import asyncio
import io
import shutil
import subprocess
import wave

import pytest

from app.services import audio as audio_service
from app.services.audio import AudioConversionError, convert_to_wav

_HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not installed")


def _make_wav_bytes(sample_rate: int = 44100, channels: int = 2, duration_ms: int = 100) -> bytes:
    """Generate a minimal valid WAV file in memory for testing.

    Args:
        sample_rate: Sample rate in Hz.
        channels: Number of audio channels.
        duration_ms: Duration in milliseconds.

    Returns:
        Raw WAV bytes.
    """
    num_frames = int(sample_rate * duration_ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * channels * num_frames)
    return buf.getvalue()


def _wav_params(data: bytes) -> wave._wave_params:
    """Parse WAV bytes and return their parameters.

    Args:
        data: WAV bytes.

    Returns:
        The ``wave`` parameter tuple.
    """
    with wave.open(io.BytesIO(data), "rb") as wf:
        return wf.getparams()


class TestConvertToWav:
    """Tests for convert_to_wav."""

    async def test_target_format_wav_is_passed_through_unchanged(self, monkeypatch):
        """16 kHz mono PCM16 WAV (the v2 client format) never touches ffmpeg."""

        async def _boom(*args, **kwargs):
            raise AssertionError("ffmpeg must not run for target-format WAV")

        monkeypatch.setattr(audio_service, "_ffmpeg_to_pcm", _boom)
        input_wav = _make_wav_bytes(sample_rate=16000, channels=1)
        assert await convert_to_wav(input_wav) == input_wav

    @needs_ffmpeg
    async def test_converts_44k_stereo_wav_to_16khz_mono(self):
        """A 44.1 kHz stereo WAV is resampled to 16 kHz mono PCM16."""
        output = await convert_to_wav(_make_wav_bytes(sample_rate=44100, channels=2))
        params = _wav_params(output)
        assert params.framerate == 16000
        assert params.nchannels == 1
        assert params.sampwidth == 2
        assert params.nframes > 0

    @needs_ffmpeg
    async def test_converts_webm_opus(self):
        """Legacy v1 clients send WebM/Opus — it decodes to a valid WAV."""
        src = _make_wav_bytes(sample_rate=48000, channels=1, duration_ms=500)
        encoded = await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
             "-c:a", "libopus", "-f", "webm", "pipe:1"],
            input=src, capture_output=True, check=True,
        )
        webm = encoded.stdout
        output = await convert_to_wav(webm)
        params = _wav_params(output)
        assert (params.framerate, params.nchannels, params.sampwidth) == (16000, 1, 2)
        assert params.nframes > 0

    async def test_invalid_audio_raises_audio_conversion_error(self):
        """Garbage bytes raise AudioConversionError (ffmpeg failure or missing)."""
        with pytest.raises(AudioConversionError):
            await convert_to_wav(b"this is not audio")

    async def test_empty_audio_raises(self):
        """Empty uploads are rejected before any decoding."""
        with pytest.raises(AudioConversionError):
            await convert_to_wav(b"")

    async def test_wav_without_frames_raises(self):
        """A header-only WAV (no samples) is treated as undecodable audio."""
        with pytest.raises(AudioConversionError):
            await convert_to_wav(_make_wav_bytes(sample_rate=16000, channels=1, duration_ms=0))

    async def test_missing_ffmpeg_raises_audio_conversion_error(self, monkeypatch):
        """If ffmpeg isn't installed, the error is typed, not FileNotFoundError."""

        async def _no_ffmpeg(*args, **kwargs):
            raise FileNotFoundError("ffmpeg")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_ffmpeg)
        with pytest.raises(AudioConversionError, match="ffmpeg is not installed"):
            await convert_to_wav(b"\x1aE\xdf\xa3 fake webm")

    async def test_ffmpeg_timeout_kills_process(self, monkeypatch):
        """A hung ffmpeg is killed after FFMPEG_TIMEOUT_S."""
        killed: list[bool] = []

        class _HungProcess:
            returncode = None

            async def communicate(self, data: bytes) -> tuple[bytes, bytes]:
                await asyncio.sleep(10)
                return b"", b""

            def kill(self) -> None:
                killed.append(True)

            async def wait(self) -> int:
                return -9

        async def _spawn(*args, **kwargs):
            return _HungProcess()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
        monkeypatch.setattr(audio_service, "FFMPEG_TIMEOUT_S", 0.05)
        with pytest.raises(AudioConversionError, match="timed out"):
            await convert_to_wav(b"\x1aE\xdf\xa3 fake webm")
        assert killed == [True]


async def test_sahara_stream_pcm_from_target_wav_skips_ffmpeg(monkeypatch):
    """B22: v2 WAV uploads become raw PCM without spawning ffmpeg."""
    from app.services.sahara_stt_stream import _to_pcm16

    async def _no_spawn(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("ffmpeg must not run for 16 kHz mono WAV")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _no_spawn)
    pcm = await _to_pcm16(_make_wav_bytes(sample_rate=16000, channels=1, duration_ms=100))
    assert len(pcm) == 1600 * 2
