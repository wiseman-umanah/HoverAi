"""Multipart upload helpers shared by /query and /agent/* (B1, B12).

Transport contract:
  - ``audio`` is always a file part. v2 clients send ``audio/wav`` (16 kHz
    mono PCM16); v1 clients send ``audio/webm``. Both are accepted — format
    handling lives in ``services/audio.py``.
  - ``screenshot`` is a file part (v2: ``image/jpeg``) *or* a legacy text
    field holding base64 PNG, optionally with a ``data:`` prefix (v1). The
    route parameter is typed ``UploadFile | str`` so FastAPI hands us
    whichever arrived. Starlette caps text fields at 1 MB before our code
    runs (that is the original B1 failure); file parts have no built-in cap,
    so it is enforced here.

Size caps are checked against ``UploadFile.size`` first (set by the
multipart parser, so oversized uploads are rejected without reading them
into memory) and again after a bounded read.
"""

import base64
import binascii

from fastapi import HTTPException, UploadFile, status

MAX_AUDIO_BYTES = 10 * 1024 * 1024        # 10 MB ≈ 5 min of 16 kHz PCM16
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024    # 8 MB — a 1600 px JPEG is ~0.3–1 MB


def _too_large(label: str, limit: int) -> HTTPException:
    """Build a 413 with a human-readable message.

    Args:
        label: What was uploaded (shown to the user).
        limit: The cap in bytes.

    Returns:
        The ``HTTPException`` to raise.
    """
    return HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"{label} is too large (limit {limit // (1024 * 1024)} MB).",
    )


async def read_capped(upload: UploadFile, limit: int, label: str) -> bytes:
    """Read an uploaded file, refusing anything over ``limit`` bytes.

    Args:
        upload: The multipart file part.
        limit: Maximum accepted size in bytes.
        label: Human-readable name used in the error message.

    Returns:
        The file contents.

    Raises:
        HTTPException 413: If the upload exceeds ``limit``.
    """
    if upload.size is not None and upload.size > limit:
        raise _too_large(label, limit)
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise _too_large(label, limit)
    return data


async def read_audio(upload: UploadFile) -> bytes:
    """Read the ``audio`` part with the audio size cap.

    Args:
        upload: The ``audio`` file part.

    Returns:
        Raw audio bytes (possibly empty — the converter rejects that with 400).

    Raises:
        HTTPException 413: If the audio exceeds ``MAX_AUDIO_BYTES``.
    """
    return await read_capped(upload, MAX_AUDIO_BYTES, "Audio recording")


async def read_screenshot(screenshot: UploadFile | str) -> bytes:
    """Return raw image bytes from a screenshot file part or legacy base64 field.

    Args:
        screenshot: ``UploadFile`` (v2 clients) or a base64 string, optionally
            prefixed with ``data:image/...;base64,`` (v1 clients).

    Returns:
        Raw image bytes (PNG/JPEG). Decoding the image itself happens later.

    Raises:
        HTTPException 400: If the screenshot is empty or not valid base64.
        HTTPException 413: If it exceeds ``MAX_SCREENSHOT_BYTES``.
    """
    if isinstance(screenshot, str):
        raw = screenshot.strip()
        if raw.startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]
        try:
            data = base64.b64decode(raw, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Screenshot could not be decoded.",
            ) from exc
        if len(data) > MAX_SCREENSHOT_BYTES:
            raise _too_large("Screenshot", MAX_SCREENSHOT_BYTES)
    else:
        data = await read_capped(screenshot, MAX_SCREENSHOT_BYTES, "Screenshot")

    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Screenshot is empty.",
        )
    return data
