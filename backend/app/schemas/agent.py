"""Agent session schemas — multi-turn agentic loop."""

from typing import Literal

from pydantic import BaseModel

# ── Outbound ───────────────────────────────────────────────────────────────────

class AgentAction(BaseModel):
    """A single action for the desktop overlay to execute.

    Mirrors ``BeaconStep`` but semantically represents what the agent DECIDED
    to do next, not a pre-planned list.  The overlay executes exactly one
    action, takes a screenshot, and calls /agent/turn.

    Attributes:
        action: Type of interaction to perform.
        instruction: Human-readable description of this action (shown in overlay).
        x: Fractional horizontal centre (0.0–1.0).
        y: Fractional vertical centre (0.0–1.0).
        w: Fractional width of bounding box.
        h: Fractional height of bounding box.
        text: Text to type — only when ``action == "type"``.
        keys: Keyboard shortcut — only when ``action == "key"``
              (e.g. ``"Ctrl+S"``, ``"Alt+F4"``).
        direction: Scroll direction — only when ``action == "scroll"``.
        amount: Scroll ticks — only when ``action == "scroll"``.
    """

    action: Literal["click", "double_click", "right_click", "type", "key", "scroll"] = "click"
    instruction: str
    x: float
    y: float
    w: float = 0.05
    h: float = 0.05
    text: str | None = None
    keys: str | None = None
    direction: Literal["up", "down"] | None = None
    amount: int | None = None


class AgentResponse(BaseModel):
    """Response from both /agent/start and /agent/turn.

    Attributes:
        session_id: Carry this forward to the next /agent/turn call.
        action: The single action the overlay should execute now.
            ``None`` when ``done=True`` — no further action needed.
        summary: Spoken feedback to the user about what Hover is doing.
        speech_b64: Base64 WAV of the summary. Empty string if TTS is
            disabled or this is an intermediate turn with no audio needed.
        done: ``True`` when the agent has achieved the goal or given up.
        turn: Current turn number (1-based). Max is ``MAX_AGENT_TURNS``.
    """

    session_id: str
    action: AgentAction | None
    summary: str
    speech_b64: str
    done: bool
    turn: int
