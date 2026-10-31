"""Agent routes — POST /api/v1/agent/start and /api/v1/agent/turn.

These endpoints power the multi-turn agentic loop.  The desktop overlay
calls /agent/start once (with the voice transcript + initial screenshot),
executes the returned action, takes a new screenshot, then calls
/agent/turn with the result.  This repeats until done=True.

Both routes accept multipart/form-data (same encoding as /query) so the
desktop app (Tauri) can build requests the same way for both endpoints:
``screenshot`` may be a file part or a legacy base64 text field, ``audio``
may be WAV or WebM, with the same size caps and error mapping as /query
(see ``app/uploads.py`` and ``app/routers/common.py``).

No request-scoped DB session is held across the LLM calls (B6).
"""

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.auth import get_current_user
from app.models.models import User
from app.routers.common import SCREENSHOT_UNDECODABLE_DETAIL, load_voice_gender, transcribe
from app.schemas.agent import AgentResponse
from app.schemas.users import VOICE_GENDER_MAP
from app.services.agent import MAX_AGENT_TURNS, agent_start, agent_turn
from app.services.images import InvalidImageError
from app.uploads import read_audio, read_screenshot

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_tts_voice(current_user: User) -> str:
    """Resolve the TTS voice for the current user's gender preference.

    Args:
        current_user: Authenticated user.

    Returns:
        Orpheus voice identifier string (e.g. ``"autumn"`` or ``"daniel"``).
    """
    voice_gender = await load_voice_gender(current_user.id)
    return VOICE_GENDER_MAP.get(voice_gender, "autumn")


def _bad_screenshot() -> HTTPException:
    """Build the 400 returned when the screenshot is not a decodable image.

    Returns:
        The ``HTTPException`` to raise.
    """
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=SCREENSHOT_UNDECODABLE_DETAIL
    )


@router.post("/start", response_model=AgentResponse)
async def agent_start_route(
    audio: UploadFile = File(..., description="Voice recording: audio/wav (v2) or audio/webm (v1)"),
    screenshot: UploadFile | str = File(
        ..., description="Screenshot file part (image/jpeg) or legacy base64 PNG text field"
    ),
    language: str = Form(default="en-pidgin", description="Hover AI language code"),
    current_user: User = Depends(get_current_user),
) -> AgentResponse:
    """Start a new agent session.

    Accepts a voice recording and the current screenshot as
    multipart/form-data — the same encoding the /query route uses so the
    desktop app (Tauri) can reuse the same request builder.

    Transcribes the audio via Groq Whisper, then starts an agent session.
    Returns the first action for the overlay to execute, a spoken reply,
    and a session_id to carry forward to /agent/turn.

    The overlay should:
      1. Execute ``response.action`` (click, key, type, etc.)
      2. Wait 600 ms for the OS to process the action
      3. Take a new screenshot
      4. POST to /agent/turn with session_id + new screenshot + action_result

    Args:
        audio: WAV (v2) or WebM (v1) voice recording.
        screenshot: Screenshot file part or legacy base64 PNG string.
        language: Hover AI language code (e.g. ``"en-pidgin"``).
        current_user: Authenticated user from JWT.

    Returns:
        ``AgentResponse`` with the first action and ``done=False``.

    Raises:
        HTTPException 400: Audio or screenshot could not be decoded.
        HTTPException 413: Upload over the size cap.
        HTTPException 422: If STT heard no speech.
        HTTPException 429/502: STT provider rate limit / failure.
        HTTPException 502: If the LLM call fails.
    """
    audio_bytes = await read_audio(audio)
    image_bytes = await read_screenshot(screenshot)
    tts_voice = await _get_tts_voice(current_user)

    # ── Transcribe audio (same pipeline as /query) ────────────────────────────
    stt_result = await transcribe(audio_bytes, language)

    try:
        return await agent_start(
            transcript=stt_result.transcript,
            screenshot=image_bytes,
            language=language,
            tts_voice=tts_voice,
        )
    except InvalidImageError as exc:
        raise _bad_screenshot() from exc
    except Exception as exc:
        logger.error("agent_start failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent failed to start — please try again.",
        ) from exc


@router.post("/turn", response_model=AgentResponse)
async def agent_turn_route(
    session_id: str = Form(..., description="Session ID from /agent/start response"),
    screenshot: UploadFile | str = File(
        ..., description="Screenshot after the last action: file part or legacy base64 PNG"
    ),
    action_result: str = Form(default="success", description="success | no_change | error"),
    current_user: User = Depends(get_current_user),
) -> AgentResponse:
    """Continue an existing agent session after an action was executed.

    The overlay calls this after executing each action.  It sends the new
    screenshot and whether the action appeared to succeed.

    The agent looks at the new screenshot, decides if the goal is achieved,
    and returns the next action — or ``done=True`` when finished.

    Overlay behaviour on receiving the response:
      - If ``done=False``: execute action, wait 600 ms, screenshot, call /turn again
      - If ``done=True``: play speech_b64, show summary, close overlay

    Args:
        session_id: Session ID from the /agent/start response.
        screenshot: Screenshot file part or legacy base64 PNG string.
        action_result: ``"success"``, ``"no_change"``, or ``"error"``.
        current_user: Authenticated user from JWT.

    Returns:
        ``AgentResponse`` with the next action or ``done=True``.

    Raises:
        HTTPException 400: Screenshot could not be decoded.
        HTTPException 404: If the session_id is not found or has expired.
        HTTPException 413: Screenshot over the size cap.
        HTTPException 502: If the LLM call fails.
    """
    image_bytes = await read_screenshot(screenshot)
    tts_voice = await _get_tts_voice(current_user)

    try:
        return await agent_turn(
            session_id=session_id,
            screenshot=image_bytes,
            action_result=action_result,
            tts_voice=tts_voice,
        )
    except InvalidImageError as exc:
        raise _bad_screenshot() from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Agent session '{session_id}' not found or expired. "
                "Sessions expire after 5 minutes of inactivity. "
                "Please start a new session."
            ),
        ) from exc
    except Exception as exc:
        logger.error("agent_turn failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent turn failed — please try again.",
        ) from exc


@router.get("/info", tags=["agent"])
async def agent_info() -> dict[str, object]:
    """Return agent configuration constants.

    Useful for the desktop overlay to know the max turns before giving up.

    Returns:
        Dict with ``max_turns`` and ``session_ttl_s``.
    """
    from app.services.agent import SESSION_TTL_S  # noqa: PLC0415
    return {
        "max_turns": MAX_AGENT_TURNS,
        "session_ttl_s": SESSION_TTL_S,
    }
