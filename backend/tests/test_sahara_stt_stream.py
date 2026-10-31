#!/usr/bin/env python3
"""
Manual test — Sahara / Intron Streaming STT.

Usage:
  cd backend
  export INTRON_API_KEY="your_key_here"     # or let it read from .env automatically
  .venv/bin/python tests/test_sahara_stt_stream.py path/to/audio.wav   # .webm / .mp3 also work
  .venv/bin/python tests/test_sahara_stt_stream.py                      # uses a 2s generated tone
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import websockets


# ── Config ────────────────────────────────────────────────────────────────────
# Read key from environment first, then fall back to .env file in parent dir.
def _load_api_key() -> str:
    key = os.environ.get("INTRON_API_KEY") or os.environ.get("SAHARA_API_KEY")
    if key:
        return key
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("INTRON_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("INTRON_API_KEY not found in environment or .env file")

API_KEY = _load_api_key()

LANGUAGE = "pcm"          # Nigerian Pidgin. Change to "en", "yo", "ha", "ig", etc.
SAMPLE_RATE = 16000
CHUNK_MS = 100            # 100 ms per chunk = 3200 bytes of PCM16 LE mono
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000

WS_URL = (
    f"wss://infer.voice.intron.io/stt/v1/stream"
    f"?use_language_asr_input={LANGUAGE}"
    f"&sample_rate={SAMPLE_RATE}"
    f"&bit_rate=16"
    f"&num_channels=1"
)

# ── Helpers ───────────────────────────────────────────────────────────────────
def to_pcm16(path: Path) -> bytes:
    """Convert any common audio file → raw PCM16 LE 16 kHz mono via ffmpeg."""
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as wf:
            if (wf.getnchannels() == 1 and wf.getsampwidth() == 2
                    and wf.getframerate() == SAMPLE_RATE):
                return wf.readframes(wf.getnframes())
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le",
         "-acodec", "pcm_s16le", "pipe:1"],
        capture_output=True, check=True,
    )
    return result.stdout


def generate_test_tone(duration_s: float = 2.0) -> bytes:
    """Generate a 440 Hz sine tone — useful for verifying connectivity."""
    import math
    import struct
    samples = int(SAMPLE_RATE * duration_s)
    return b"".join(
        struct.pack("<h", int(16000 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE)))
        for i in range(samples)
    )


# ── Main streaming logic ──────────────────────────────────────────────────────
async def stream_transcribe(pcm: bytes) -> None:
    headers = {"Authorization": f"Bearer {API_KEY}"}
    print(f"Connecting → {WS_URL}")

    async with websockets.connect(WS_URL, additional_headers=headers, max_size=2**22) as ws:
        # 1. Wait for SESSION_CREATED
        msg = json.loads(await ws.recv())
        if msg.get("message_type") != "SESSION_CREATED":
            print("Unexpected first message:", msg)
            return
        print(
            f"SESSION_CREATED  "
            f"session_id={msg.get('session_id')}  "
            f"credits={msg.get('credit_balance')}"
        )

        # 2. Stream audio in 100 ms chunks
        offset = 0
        chunk_id = 0
        while offset < len(pcm):
            chunk = pcm[offset:offset + CHUNK_BYTES]
            if len(chunk) < 1024:           # API minimum chunk size
                chunk = chunk.ljust(1024, b"\x00")
            await ws.send(json.dumps({
                "message_type": "INPUT_AUDIO_CHUNK",
                "audio_base_64": base64.b64encode(chunk).decode(),
                "ack_id": chunk_id,
            }))
            offset += CHUNK_BYTES
            chunk_id += 1

            # Drain any replies that arrived without blocking
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.01)
                    data = json.loads(raw)
                    mtype = data.get("message_type")
                    if mtype == "PARTIAL_TRANSCRIPT":
                        print(f"  PARTIAL  → {data.get('transcript')!r}")
                    elif mtype == "AUDIO_CHUNK_ACK":
                        pass  # quiet
                    else:
                        print(f"  {mtype}: {data}")
            except asyncio.TimeoutError:
                pass

        # 3. Commit — signals end of audio
        print("Sending COMMIT …")
        await ws.send(json.dumps({"message_type": "COMMIT"}))

        # 4. Wait for final result
        while True:
            data = json.loads(await ws.recv())
            mtype = data.get("message_type")
            if mtype == "COMMITTED_TRANSCRIPT":
                print("\n✅ FINAL TRANSCRIPT:")
                print(data.get("transcript_text"))
                print(
                    f"(audio_len={data.get('audio_len')}s  "
                    f"id={data.get('transcript_id')})"
                )
                break
            elif mtype == "PARTIAL_TRANSCRIPT":
                print(f"  PARTIAL  → {data.get('transcript')!r}")
            elif mtype in (
                "ERROR", "INPUT_ERROR", "AUTHENTICATION_ERROR",
                "QUOTA_EXCEEDED", "RESOURCE_EXHAUSTED",
            ):
                print("❌", mtype, data)
                break
            else:
                print(f"  {mtype}: {data}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1:
        audio_path = Path(sys.argv[1])
        if not audio_path.exists():
            sys.exit(f"File not found: {audio_path}")
        print(f"Converting {audio_path} → PCM16 …")
        pcm = to_pcm16(audio_path)
    else:
        print("No audio file given — generating 2s test tone")
        pcm = generate_test_tone(2.0)

    print(f"PCM size: {len(pcm)} bytes ({len(pcm) / SAMPLE_RATE / 2:.1f}s)")
    asyncio.run(stream_transcribe(pcm))
