"""Screenshot decoding and compression shared by the vision and agent pipelines.

Design decisions:
  - Callers pass raw image bytes. The transport layer (``app/uploads.py``)
    already turned either a file part (v2 client: JPEG) or a legacy base64
    text field (v1 client: PNG) into bytes, so nothing here knows about
    base64 input or ``data:`` URIs any more (B1).
  - Every image is re-encoded to a small JPEG, even one the client already
    resized: the vision model's token budget depends on the exact target
    size, which is a server-side decision.
  - ``compress_to_jpeg_b64`` is CPU-bound PIL work; async callers must run
    it via ``asyncio.to_thread`` (B4).
  - An undecodable image raises ``InvalidImageError`` (→ HTTP 400) instead of
    forwarding garbage to the LLM and surfacing a confusing 502.
"""

import base64
import io
import logging

from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

# Decompression-bomb guard: a 8K display is ~33 MP; anything far beyond that
# is not a screenshot. PIL raises DecompressionBombError past 2x this value.
Image.MAX_IMAGE_PIXELS = 50_000_000


class InvalidImageError(Exception):
    """Raised when the screenshot bytes cannot be decoded as an image."""


def compress_to_jpeg_b64(
    image_bytes: bytes,
    max_width: int,
    max_height: int,
    quality: int,
) -> str:
    """Decode an image, fit it within a box, and re-encode it as JPEG.

    Args:
        image_bytes: Raw PNG/JPEG/WebP/… bytes.
        max_width: Maximum output width in pixels (aspect ratio preserved).
        max_height: Maximum output height in pixels.
        quality: JPEG quality (1–95).

    Returns:
        Plain base64 (no ``data:`` prefix) of the compressed JPEG.

    Raises:
        InvalidImageError: If the bytes are empty or not a decodable image.
    """
    if not image_bytes:
        raise InvalidImageError("Screenshot is empty")
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImageError(f"Screenshot could not be decoded: {exc}") from exc

    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    compressed = out.getvalue()
    logger.debug(
        "Screenshot compressed: %dkB → %dkB (%dx%d)",
        len(image_bytes) // 1024,
        len(compressed) // 1024,
        img.width,
        img.height,
    )
    return base64.b64encode(compressed).decode()
