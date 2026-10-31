"""Agentic loop service — multi-turn screen-aware task execution.

Architecture
────────────
The agent maintains a conversation history (list of messages) in an
in-process session store keyed by session_id.  Each turn:

  1. The desktop overlay sends the CURRENT screenshot + result of the
     last action.
  2. The agent appends the screenshot + result to the conversation.
  3. It calls Qwen3 with the full history: "given everything you've done
     so far and what the screen looks like now, what is the ONE next action?"
  4. Returns a single AgentAction — the overlay executes it, takes a new
     screenshot, and calls /agent/turn.
  5. If the model says goal_achieved=true (or MAX_AGENT_TURNS reached),
     the session is closed and done=True is returned.

Why one-action-at-a-time instead of a pre-planned list
───────────────────────────────────────────────────────
Pre-planning all steps upfront means later steps are blind guesses about
the future screen state.  One action + observe + decide is the canonical
ReAct (Reason + Act) pattern — the model adapts after every action based
on what actually happened, rather than following a rigid script.

Session store
─────────────
Sessions are stored in-process (dict).  On Render free tier with a single
worker this is fine.  Sessions auto-expire after SESSION_TTL_S seconds of
inactivity and are cleaned up lazily on next access.

Token budget
────────────
Each turn sends the full history.  To stay under Groq's 7k ITPM:
  - Screenshots are compressed to 512×288 q=35 (the tighter budget)
    because we may have up to MAX_AGENT_TURNS images in history.
  - Only the LAST 3 screenshots are kept in history; earlier ones are
    replaced with a text summary: "[screenshot N — action X executed]".
  - Each turn's total budget: ~500 (prompt) + ~1,200 (1 screenshot) +
    ~200 (history summaries) + ~300 (response) ≈ 2,200 tokens — well
    under 7k even with 3 prior turns in context.
"""

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any

from app.config import settings
from app.schemas.agent import AgentAction, AgentResponse
from app.services.images import compress_to_jpeg_b64
from app.services.llm_client import groq_client

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_AGENT_TURNS: int = 6          # hard cap — prevents runaway loops
SESSION_TTL_S: float = 300.0      # 5 min inactivity → session expires
# Minimum gap between turns — keeps us under Groq's 30 RPM cap.
MIN_TURN_INTERVAL_S: float = 3.0
# 400×225 q=28 keeps image tokens ~500-800 per turn.
_AGENT_MAX_WIDTH = 400
_AGENT_MAX_HEIGHT = 225
_AGENT_JPEG_QUALITY = 28

# ── Session store ─────────────────────────────────────────────────────────────

class _Session:
    """In-memory agent session."""

    def __init__(self, session_id: str, goal: str, language: str) -> None:
        self.session_id = session_id
        self.goal = goal
        self.language = language
        self.turn: int = 0
        self.last_access: float = time.monotonic()
        self.last_turn_time: float = 0.0  # monotonic time of last completed turn
        # OpenAI-style messages list — system + alternating user/assistant
        self.messages: list[dict[str, Any]] = []
        # Text summaries of past screenshots (to save tokens)
        self.action_history: list[str] = []

    def touch(self) -> None:
        self.last_access = time.monotonic()

    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_access) > SESSION_TTL_S


_sessions: dict[str, _Session] = {}


def _cleanup_expired() -> None:
    """Remove expired sessions from the store (lazy GC)."""
    expired = [sid for sid, s in _sessions.items() if s.is_expired()]
    for sid in expired:
        del _sessions[sid]
        logger.debug("Agent session expired and removed: %s", sid)


# ── Image compression ─────────────────────────────────────────────────────────

def _compress(image_bytes: bytes) -> str:
    """Compress a screenshot to fit within per-turn token budget.

    Tighter than the single-shot query pipeline because the agent keeps
    multiple turns in context — total token cost compounds. CPU-bound: call
    through ``asyncio.to_thread`` (B4).

    Args:
        image_bytes: Raw PNG/JPEG bytes of the screenshot.

    Returns:
        Plain base64 JPEG string at ``_AGENT_MAX_WIDTH``×``_AGENT_MAX_HEIGHT``
        resolution.

    Raises:
        InvalidImageError: If the bytes are not a decodable image.
    """
    return compress_to_jpeg_b64(
        image_bytes, _AGENT_MAX_WIDTH, _AGENT_MAX_HEIGHT, _AGENT_JPEG_QUALITY
    )


# ── Prompt helpers ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are Hover — an AI agent that controls a computer on behalf of the user.

You operate in a ReAct loop: you see the current screen, decide ONE action, \
the system executes it, then you see the result and decide the next action. \
You keep going until the goal is achieved or you give up after exhausting options.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSE FORMAT — raw JSON only, no markdown, no explanation, no <think> tags:
{
  "reasoning": "one short sentence",
  "action": "click",
  "instruction": "Click the Chrome icon on the desktop",
  "x": 0.08,
  "y": 0.12,
  "w": 0.06,
  "h": 0.06,
  "text": null,
  "keys": null,
  "direction": null,
  "amount": null,
  "spoken_reply": "Found Chrome on your desktop — clicking it now!",
  "goal_achieved": false
}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FIELDS:
- reasoning: ONE sentence max — what you see and why this action. Keep it short.
- action: one of: click | double_click | right_click | type | key | scroll
- instruction: Short label shown to the user in the overlay (under 15 words)
- x, y: EXACT centre of the target as fractions 0.0-1.0. Be precise.
- w, h: Bounding box size as fractions (use 0.04-0.08 for icons, 0.12-0.2 for buttons)
- text: string to type — only for action=type, else null
- keys: shortcut string — only for action=key, e.g. "Ctrl+S", "Alt+F4", "Win+D", else null
- direction: "up" or "down" — only for action=scroll, else null
- amount: scroll ticks (3-10) — only for action=scroll, else null
- spoken_reply: What Hover says to the user. Warm, direct, under 2 sentences. \
  Use "I", "you", "your". Never third person. Matches user's language.
- goal_achieved: true ONLY when the goal is fully accomplished. false otherwise.

COORDINATE PRECISION:
- Look at the screenshot carefully. Scan methodically top-left to bottom-right.
- For desktop icons (grid layout): first column x≈0.03-0.07, row 1 y≈0.08-0.14
  Columns spaced ~0.08 apart. Rows spaced ~0.12 apart.
- For Windows taskbar: y≈0.96-0.99. Icons spaced ~0.04 apart horizontally.
- For macOS dock: y≈0.96-0.99. Icons centred horizontally.
- For window title bar buttons (close/min/max): top-right corner, y≈0.01-0.03
- For browser address bar: y≈0.04-0.06, centred x≈0.5
- x=0.5, y=0.5 is ONLY for keyboard actions that need no mouse movement

ADAPTING FROM FEEDBACK:
- If action_result="no_change": your last click probably missed. Look more carefully \
  at the screenshot — the element might be at slightly different coordinates. \
  Try again with corrected coordinates, or try a different approach.
- If action_result="error": something unexpected happened. Look at the current \
  screenshot and decide how to recover. If the wrong app opened, close it first.
- If action_result="success": the action worked. Decide what to do next.

WHEN TO SET goal_achieved=true:
- The user's original request is FULLY done — not just started, but completed.
- Example: asked to open Chrome → Chrome window is visible and in focus → true
- Example: asked to save a file → Ctrl+S sent → assume saved → true
- Example: asked to open a file → file is now open in the editor → true
- Example: asked to write/type text in an app → the text has been TYPED (action=type executed) → true
- IMPORTANT: If the goal involves typing text, you must FIRST click to focus the input,
  THEN type the text. Do NOT set goal_achieved=true after just clicking — the text has
  not been typed yet. Only set goal_achieved=true AFTER the type action has been sent.
- IMPORTANT: clicking an app window to focus it is NOT the goal — it is only a step.
  If the user asked you to write/type something, the goal is only done once the text action runs.

WHEN TO GIVE UP (return goal_achieved=true with a helpful spoken_reply):
- After 3 attempts at the same action with no_change results
- If the screen state makes the goal impossible (wrong OS, locked screen, etc.)
- Say something like: "I'm having trouble finding that — you might need to do this one manually."
"""


def _strip_thinking(raw: str) -> str:
    """Remove Qwen3 <think>...</think> blocks before JSON parsing."""
    return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()


def _extract_json(raw: str) -> str:
    """Extract the JSON object from a model response.

    Handles all the ways small models misbehave:
      - Bare JSON (correct)                         → returned as-is
      - ```json ... ``` fences                      → fences stripped
      - ``` ... ``` fences (no language tag)        → fences stripped
      - Leading/trailing prose around the JSON      → first {...} extracted
      - <think>...</think> blocks (Qwen3)           → stripped first

    The JSON content itself is never rewritten.

    Args:
        raw: Raw model output string.

    Returns:
        The extracted JSON string, ready for ``json.loads``.

    Raises:
        ValueError: If no JSON object can be found.
    """
    text = _strip_thinking(raw).strip()

    # 1. Strip any markdown code fence (```json ... ``` or ``` ... ```)
    #    Use a regex so we handle multi-line fences reliably.
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # 2. If the text doesn't start with { try to pull out the first {...} block.
    #    Some models prefix with prose like "Here is the JSON:".
    if not text.startswith("{"):
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            text = brace_match.group(0)

    # NOTE: no numeric "cleanup" here. Earlier regexes that stripped leading
    # zeros turned 0.08 into 0.8 and "0803" into "803" inside strings (B2).

    return text


def _parse_agent_response(raw: str) -> dict[str, Any]:
    """Parse and validate the model's JSON response.

    Args:
        raw: Raw model output string.

    Returns:
        Parsed dict with all required fields present.

    Raises:
        ValueError: If the response is not valid JSON or missing required fields.
    """
    try:
        cleaned = _extract_json(raw)
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model returned invalid JSON: {raw[:200]}") from exc

    required = {"action", "instruction", "x", "y", "spoken_reply", "goal_achieved"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Model response missing fields: {missing}")

    return data


def _build_action(data: dict[str, Any]) -> AgentAction:
    """Build an AgentAction from a parsed model response dict.

    Args:
        data: Parsed model response dictionary.

    Returns:
        A validated ``AgentAction`` instance.
    """
    return AgentAction(
        action=data.get("action", "click"),
        instruction=data.get("instruction", ""),
        x=float(data.get("x", 0.5)),
        y=float(data.get("y", 0.5)),
        w=float(data.get("w", 0.05)),
        h=float(data.get("h", 0.05)),
        text=data.get("text"),
        keys=data.get("keys"),
        direction=data.get("direction"),
        amount=data.get("amount"),
    )


def _screenshot_user_content(
    compressed_b64: str,
    text_content: str,
) -> list[dict[str, Any]]:
    """Build a multimodal content block with an image + text.

    Args:
        compressed_b64: Plain base64 JPEG string (no data URI prefix).
        text_content: Text part of the user message.

    Returns:
        A list of content blocks for the OpenAI messages format.
    """
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{compressed_b64}"},
        },
        {"type": "text", "text": text_content},
    ]


# ── Public API ────────────────────────────────────────────────────────────────

async def agent_start(
    transcript: str,
    screenshot: bytes,
    language: str,
    tts_voice: str,
) -> AgentResponse:
    """Start a new agent session — first turn.

    Creates a session, builds the initial conversation, calls the model, and
    returns the first action for the overlay to execute.

    Args:
        transcript: User's voice instruction (already transcribed).
        screenshot: Raw PNG/JPEG bytes of the current screen.
        language: Hover AI language code.
        tts_voice: Orpheus TTS voice to use for spoken_reply.

    Returns:
        ``AgentResponse`` with ``session_id``, the first ``action``, and
        ``done=False`` (or ``done=True`` if the goal is trivially achieved).

    Raises:
        InvalidImageError: If the screenshot is not a decodable image.
    """
    _cleanup_expired()

    # Compress first so an invalid image never leaves an orphaned session.
    compressed = await asyncio.to_thread(_compress, screenshot)

    session_id = secrets.token_hex(16)
    session = _Session(session_id=session_id, goal=transcript, language=language)
    _sessions[session_id] = session

    # Initial user message — goal + first screenshot
    first_user_content = _screenshot_user_content(
        compressed,
        (
            f"Goal (language: {language}): {transcript}\n\n"
            "This is the current state of the screen. "
            "What is the first action to take?"
        ),
    )

    session.messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": first_user_content},
    ]

    return await _run_turn(session, tts_voice)


async def agent_turn(
    session_id: str,
    screenshot: bytes,
    action_result: str,
    tts_voice: str,
) -> AgentResponse:
    """Continue an existing agent session — subsequent turns.

    Appends the action result + new screenshot to the conversation history
    and calls the model to decide the next action.

    Args:
        session_id: Session ID from the previous response.
        screenshot: Raw PNG/JPEG bytes taken AFTER the last action executed.
        action_result: ``"success"``, ``"no_change"``, or ``"error"``.
        tts_voice: Orpheus TTS voice to use for spoken_reply.

    Returns:
        ``AgentResponse`` with the next ``action``, or ``done=True`` if
        the goal is achieved or the session has expired/not found.

    Raises:
        KeyError: If ``session_id`` is not found or has expired.
        InvalidImageError: If the screenshot is not a decodable image.
    """
    _cleanup_expired()

    session = _sessions.get(session_id)
    if session is None or session.is_expired():
        raise KeyError(f"Agent session not found or expired: {session_id}")

    session.touch()

    # ── Rate-limit guard — respect Groq's 30 RPM cap ─────────────────────────
    elapsed_since_last = time.monotonic() - session.last_turn_time
    if session.last_turn_time > 0 and elapsed_since_last < MIN_TURN_INTERVAL_S:
        wait = MIN_TURN_INTERVAL_S - elapsed_since_last
        logger.debug("Agent rate-limit sleep %.2fs for session %s", wait, session_id)
        await asyncio.sleep(wait)

    compressed = await asyncio.to_thread(_compress, screenshot)
    result_label = {
        "success": "✓ Action succeeded",
        "no_change": "✗ No change — the action may have missed the target",
        "error": "✗ Something unexpected happened",
    }.get(action_result, action_result)

    # Append feedback + new screenshot as the next user message
    turn_content = _screenshot_user_content(
        compressed,
        (
            f"{result_label}. This is the screen now.\n"
            "What is the next action to take toward the goal?"
        ),
    )

    session.messages.append({"role": "user", "content": turn_content})

    return await _run_turn(session, tts_voice)


async def _run_turn(session: _Session, tts_voice: str) -> AgentResponse:
    """Execute one model turn and return an AgentResponse.

    Calls Qwen3, parses the response, synthesises TTS for the spoken reply,
    updates session state, and returns the action for the overlay.

    Args:
        session: The current agent session (mutated in place).
        tts_voice: Orpheus TTS voice identifier.

    Returns:
        A populated ``AgentResponse``.
    """
    session.turn += 1
    session.touch()

    # Hard stop — prevent runaway loops
    if session.turn > MAX_AGENT_TURNS:
        logger.warning("Agent session %s exceeded MAX_AGENT_TURNS", session.session_id)
        speech_b64 = await _safe_tts(
            "I've tried as many times as I can — you might need to take over from here!",
            tts_voice,
        )
        _sessions.pop(session.session_id, None)
        return AgentResponse(
            session_id=session.session_id,
            action=None,
            summary="Reached maximum attempts — handing back control.",
            speech_b64=speech_b64,
            done=True,
            turn=session.turn,
        )

    # ── Prune BEFORE calling the model ───────────────────────────────────────
    # Must happen here so images are already removed when token cost is counted.
    _prune_history(session)

    # ── Call the model ────────────────────────────────────────────────────────
    raw = ""
    for attempt in range(2):
        if attempt == 1:
            session.messages.append({
                "role": "user",
                "content": (
                    "Your previous response was not valid JSON. "
                    "Return ONLY the raw JSON object, no markdown, no <think> tags."
                ),
            })
        try:
            resp = await groq_client.chat.completions.create(
                model=settings.vision_model,
                messages=session.messages,  # type: ignore[arg-type]
                temperature=0.15,
                max_tokens=1200,
            )
            raw = resp.choices[0].message.content or ""
            logger.debug(
                "Agent LLM raw response (turn %d, attempt %d): %s",
                session.turn, attempt + 1, raw[:500],
            )
            break
        except Exception as exc:
            logger.error("Agent LLM error (turn %d, attempt %d): %s", session.turn, attempt + 1, exc)
            if attempt == 1:
                speech_b64 = await _safe_tts("Something went wrong — please try again.", tts_voice)
                return AgentResponse(
                    session_id=session.session_id,
                    action=None,
                    summary="LLM error — please try again.",
                    speech_b64=speech_b64,
                    done=True,
                    turn=session.turn,
                )

    # ── Parse response ────────────────────────────────────────────────────────
    try:
        data = _parse_agent_response(raw)
    except ValueError as exc:
        logger.error("Agent parse error: %s | raw was: %r", exc, raw[:600])
        speech_b64 = await _safe_tts("I got confused — please try again.", tts_voice)
        return AgentResponse(
            session_id=session.session_id,
            action=None,
            summary="Could not parse model response.",
            speech_b64=speech_b64,
            done=True,
            turn=session.turn,
        )

    spoken_reply: str = data.get("spoken_reply", "On it!")
    goal_achieved: bool = bool(data.get("goal_achieved", False))
    action = _build_action(data)

    # ── Append model response to history ─────────────────────────────────────
    session.messages.append({"role": "assistant", "content": raw})

    # ── Record a lightweight text summary of this action for future turns ────
    action_summary = (
        f"Turn {session.turn}: {action.action} at ({action.x:.2f}, {action.y:.2f})"
        + (f" keys={action.keys}" if action.keys else "")
        + (f" text={action.text!r}" if action.text else "")
    )
    session.action_history.append(action_summary)
    session.last_turn_time = time.monotonic()  # stamp for rate-limit enforcement
    logger.info("Agent %s %s", session.session_id, action_summary)

    # ── TTS for spoken reply (only on turn 1 and when done) ──────────────────
    # Intermediate turns skip TTS to reduce latency — the overlay shows
    # the instruction text as visual feedback instead.
    needs_tts = session.turn == 1 or goal_achieved
    speech_b64 = await _safe_tts(spoken_reply, tts_voice) if needs_tts else ""

    # ── Clean up if done ──────────────────────────────────────────────────────
    if goal_achieved:
        _sessions.pop(session.session_id, None)
        logger.info("Agent session %s completed in %d turns", session.session_id, session.turn)

    return AgentResponse(
        session_id=session.session_id,
        action=None if goal_achieved else action,
        summary=spoken_reply,
        speech_b64=speech_b64,
        done=goal_achieved,
        turn=session.turn,
    )


def _prune_history(session: _Session) -> None:
    """Replace ALL previous screenshots with text summaries — keep only the last.

    Called BEFORE every LLM request so at most one image is ever sent.
    This keeps the token cost bounded regardless of how many turns have run:

      system prompt  ~600 tokens  (fixed)
      1 screenshot   ~500-800     (400x225 q=28)
      text history   ~150/turn    (grows slowly)
      response       ~300         (max_tokens=400)

    Any earlier user message that contained an image_url block has the image
    stripped and replaced with a short text description of what was there.

    Args:
        session: Agent session to prune (mutated in place).
    """
    user_image_indices = [
        i for i, m in enumerate(session.messages)
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and any(
            isinstance(b, dict) and b.get("type") == "image_url"
            for b in m["content"]
        )
    ]

    # Keep only the LAST (current) screenshot — replace all earlier ones
    to_replace = user_image_indices[:-1]

    for idx in to_replace:
        msg = session.messages[idx]
        text_parts = [
            b["text"] for b in msg["content"]
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        turn_num = len([h for h in session.action_history if h])
        label = f"[turn {turn_num} screenshot removed]"
        summary = (" ".join(text_parts)[:80] + " " + label).strip()
        session.messages[idx] = {"role": "user", "content": summary}
        logger.debug("Pruned screenshot at message index %d", idx)


async def _safe_tts(text: str, voice: str) -> str:
    """Synthesise speech, returning empty string on any failure.

    Args:
        text: Text to speak.
        voice: Orpheus voice identifier.

    Returns:
        Base64 WAV string, or ``""`` on error or if TTS is disabled.
    """
    if not settings.tts_enabled:
        return ""
    try:
        from app.services.tts import synthesise  # noqa: PLC0415
        speech_b64, _ = await synthesise(text, voice=voice)
        return speech_b64
    except Exception as exc:
        logger.error("Agent TTS error: %s", exc)
        return ""
