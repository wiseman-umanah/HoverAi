"""Vision pipeline schemas — screen context in, grounded steps out.

Guided-assistant mode: the AI produces human-readable step-by-step
instructions that the user follows manually. Since Phase 3 each step may
also carry a ``target`` — the on-screen box the desktop overlay draws a
beacon on.

Grounding design (AGENTIC_PLAN D2):
  - The desktop sends a ``ScreenContext``: app, window title, cursor and a
    list of interactive elements from the OS accessibility API, each with an
    ``id`` that is also drawn as a numbered mark on the screenshot
    ("set-of-marks").
  - The model points at elements by id. The server then copies that
    element's bounds into ``StepTarget`` — model-emitted coordinates are never
    trusted when an id is given. A raw bbox is only a fallback for things that
    have no element (``element_id`` is ``None`` then).
  - All coordinates are normalised 0–1 relative to the screenshot, origin
    top-left, ``x``/``y`` = top-left corner of the box.

Context validation is deliberately forgiving: values are clipped into
[0, 1], over-long strings are truncated, the element list is capped, and a
single malformed element is dropped instead of rejecting the whole context
(a broken context must never fail a query — see ``services/grounding.py``).
"""

import logging
import math
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

MAX_CONTEXT_ELEMENTS = 200
MAX_NAME_CHARS = 100
MAX_ROLE_CHARS = 32
MAX_TITLE_CHARS = 200


def _clip01(value: float) -> float:
    """Clamp a finite float into [0, 1].

    Args:
        value: Any float.

    Returns:
        ``value`` clamped to [0, 1].

    Raises:
        ValueError: If ``value`` is NaN or infinite.
    """
    if not math.isfinite(value):
        raise ValueError("coordinate must be finite")
    return min(1.0, max(0.0, value))


def _truncate(value: str | None, limit: int) -> str | None:
    """Strip, flatten newlines and truncate a free-text field.

    Args:
        value: Raw string (or ``None``).
        limit: Maximum number of characters kept.

    Returns:
        The cleaned string, or ``None`` for ``None``/blank input.
    """
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned[:limit] or None


class NormPoint(BaseModel):
    """A point in normalised screenshot coordinates.

    Attributes:
        x: Horizontal position, 0 = left edge, 1 = right edge.
        y: Vertical position, 0 = top edge, 1 = bottom edge.
    """

    x: float
    y: float

    @field_validator("x", "y")
    @classmethod
    def _clip(cls, v: float) -> float:
        """Clip to [0, 1]."""
        return _clip01(v)


class NormRect(BaseModel):
    """A box in normalised screenshot coordinates (top-left + size).

    Values are clipped so the box lies inside the image: ``x``/``y`` in
    [0, 1] and ``x + w`` / ``y + h`` ≤ 1.

    Attributes:
        x: Left edge.
        y: Top edge.
        w: Width.
        h: Height.
    """

    x: float
    y: float
    w: float
    h: float

    @model_validator(mode="after")
    def _clip_box(self) -> Self:
        """Clip the box into the unit square."""
        self.x = _clip01(self.x)
        self.y = _clip01(self.y)
        self.w = min(_clip01(self.w), 1.0 - self.x)
        self.h = min(_clip01(self.h), 1.0 - self.y)
        return self


class ContextElement(NormRect):
    """One interactive element from the OS accessibility tree.

    Attributes:
        id: Mark number drawn on the screenshot next to this element's box.
        role: Accessibility role, lower-cased (``"button"``, ``"edit"``, …).
        name: Accessible name / label (truncated to ``MAX_NAME_CHARS``).
        enabled: ``False`` for disabled (greyed-out) elements.
        focused: ``True`` for the element with keyboard focus (the client
            only sends the key when it is true).
    """

    id: int = Field(ge=0)
    role: str = ""
    name: str = ""
    enabled: bool = True
    focused: bool = False

    @field_validator("role", mode="before")
    @classmethod
    def _clean_role(cls, v: Any) -> str:
        """Lower-case and truncate the role."""
        return (_truncate(v, MAX_ROLE_CHARS) or "").lower()

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, v: Any) -> str:
        """Flatten whitespace and truncate the name."""
        return _truncate(v, MAX_NAME_CHARS) or ""


class ScreenContext(BaseModel):
    """Structured screen facts sent by the desktop alongside the screenshot.

    Attributes:
        app: Active application / process name, if known.
        window_title: Active window title, if known.
        window: Active window bounds, normalised to the screenshot.
        cursor: Mouse cursor position, normalised to the screenshot.
        elements: Up to ``MAX_CONTEXT_ELEMENTS`` interactive elements, in the
            order the client sent them. Elements that fail validation, have
            an empty box after clipping, or repeat an earlier id are dropped.
    """

    app: str | None = None
    window_title: str | None = None
    window: NormRect | None = None
    cursor: NormPoint | None = None
    elements: list[ContextElement] = Field(default_factory=list)

    @field_validator("app", mode="before")
    @classmethod
    def _clean_app(cls, v: Any) -> str | None:
        """Truncate the app name."""
        return _truncate(v, MAX_NAME_CHARS)

    @field_validator("window_title", mode="before")
    @classmethod
    def _clean_title(cls, v: Any) -> str | None:
        """Truncate the window title."""
        return _truncate(v, MAX_TITLE_CHARS)

    @field_validator("window", "cursor", mode="wrap")
    @classmethod
    def _optional_geometry(cls, v: Any, handler: Any) -> Any:
        """Treat an invalid window/cursor as absent instead of failing."""
        try:
            return handler(v)
        except ValueError:
            logger.warning("Screen context: ignoring invalid window/cursor %r", v)
            return None

    @field_validator("elements", mode="before")
    @classmethod
    def _lenient_elements(cls, v: Any) -> list[ContextElement]:
        """Validate elements one by one, dropping bad ones and capping the list."""
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("elements must be a list")
        out: list[ContextElement] = []
        seen: set[int] = set()
        dropped = 0
        for raw in v:
            if len(out) >= MAX_CONTEXT_ELEMENTS:
                dropped += 1
                continue
            try:
                el = ContextElement.model_validate(raw)
            except ValueError:
                dropped += 1
                continue
            if el.id in seen or el.w <= 0 or el.h <= 0:
                dropped += 1
                continue
            seen.add(el.id)
            out.append(el)
        if dropped:
            logger.warning("Screen context: dropped %d of %d elements", dropped, len(v))
        return out

    def element(self, element_id: int) -> ContextElement | None:
        """Look up an element by its mark id.

        Args:
            element_id: The mark number.

        Returns:
            The element, or ``None`` if no element has that id.
        """
        for el in self.elements:
            if el.id == element_id:
                return el
        return None


class StepTarget(BaseModel):
    """Where the overlay should draw a step's beacon.

    Attributes:
        element_id: Mark id of the element the step acts on, or ``None`` when
            the box is a model-estimated fallback bbox.
        x: Left edge, normalised 0–1 to the screenshot.
        y: Top edge.
        w: Width.
        h: Height.
    """

    element_id: int | None = None
    x: float
    y: float
    w: float
    h: float


class BeaconStep(BaseModel):
    """A single guided instruction step shown in the overlay panel.

    The user reads the instruction and performs it manually.

    Attributes:
        step: 1-based step number in the sequence.
        instruction: Full human-readable instruction for this step, written
                     in the user's preferred language.  Should be a complete
                     sentence describing exactly what to do, e.g.:
                     "Click on cell B2 to select it."
                     "Press Ctrl+Shift+L to apply a filter."
        keys: Optional keyboard shortcut to highlight in the overlay
              (e.g. ``"Ctrl+S"``).  ``None`` if no shortcut is involved.
        tip: Optional extra context or warning for this step — shown in a
             smaller font below the instruction.  ``None`` if not needed.
        target: On-screen box to highlight, resolved server-side (see
            ``services/grounding.resolve_targets``). ``None`` for steps with
            nothing visible to point at (keyboard shortcuts, waiting, things
            that only appear after an earlier step).
    """

    step: int = Field(ge=1)
    instruction: str
    keys: str | None = None
    tip: str | None = None
    target: StepTarget | None = None


class ModelStep(BaseModel):
    """A step exactly as the LLM emitted it, before target resolution.

    Attributes:
        step: 1-based step number.
        instruction: Instruction text.
        keys: Optional keyboard shortcut.
        tip: Optional tip.
        target: The raw ``target`` value from the model's JSON — usually
            ``{"element_id": 12}``, ``{"bbox": [x, y, w, h]}`` or ``null``,
            but untrusted and possibly malformed. Interpreted only by
            ``resolve_targets``.
    """

    step: int = Field(ge=1)
    instruction: str
    keys: str | None = None
    tip: str | None = None
    target: Any = None


class VisionResult(BaseModel):
    """Output from the vision + LLM guidance pipeline.

    Attributes:
        steps: Ordered list of guided instruction steps for the overlay panel.
        summary: Warm one-sentence spoken summary of what the AI understood
                 and is about to guide the user through.  Read aloud via TTS.
        usage: Provider token usage for the call that produced this result
            (``prompt_tokens``, ``completion_tokens``, ``total_tokens``) when
            reported. Diagnostic only — not part of the ``/query`` response.
    """

    steps: list[BeaconStep]
    summary: str
    usage: dict[str, int] | None = None
