"""Grounding — screen context parsing, prompt rendering and target resolution.

Phase 3 (AGENTIC_PLAN D2): the model points at elements by mark id, the
server owns the geometry. Three pure helpers live here so they can be unit
tested without the LLM:

  - ``parse_screen_context`` turns the ``context`` multipart text field into
    a ``ScreenContext``. It never raises: an oversized or malformed context
    is logged and ignored, because a query must still work without it.
  - ``render_context`` renders the context as compact text for the user
    message. One line per element, e.g. ``[12] button "Save" @.10,.20 .05x.03``,
    with `` (focused)`` appended for the element that has keyboard focus
    (leading zeros dropped — 200 elements are ~8 KB / ~3.7k tokens).
    Disabled elements are omitted from the list, but ids are never
    renumbered: they must match the marks drawn on the screenshot.
  - ``resolve_targets`` turns the model's raw per-step ``target`` into a
    trusted ``StepTarget``:

      * a known ``element_id`` → that element's bounds from the request
        (model coordinates are ignored);
      * an unknown / malformed id → ``None`` (logged);
      * no id but a ``bbox`` → the bbox, clipped to [0, 1], with
        ``element_id=None``. Degenerate boxes (w or h ≤ 0, or > 0.9) and
        boxes that are clearly not normalised (a value outside
        [-0.05, 1.05], i.e. pixels) are rejected → ``None``.
"""

import json
import logging
import math
from typing import Any

from pydantic import ValidationError

from app.schemas.vision import BeaconStep, ModelStep, ScreenContext, StepTarget

logger = logging.getLogger(__name__)

MAX_CONTEXT_BYTES = 200 * 1024
_MAX_BBOX_SIDE = 0.9
_BBOX_TOLERANCE = 0.05
_PROMPT_NAME_CHARS = 60


def parse_screen_context(raw: str | None) -> ScreenContext | None:
    """Parse the ``context`` form field, ignoring it if it is unusable.

    Args:
        raw: The JSON string the desktop sent (``""``/``None`` if absent).

    Returns:
        A validated ``ScreenContext``, or ``None`` if the field is absent,
        larger than ``MAX_CONTEXT_BYTES``, not JSON, or not an object.
    """
    if raw is None or not raw.strip():
        return None
    size = len(raw.encode("utf-8"))
    if size > MAX_CONTEXT_BYTES:
        logger.warning("Ignoring screen context: %d bytes > %d", size, MAX_CONTEXT_BYTES)
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("context must be a JSON object")
        return ScreenContext.model_validate(data)
    except (ValueError, ValidationError) as exc:
        logger.warning("Ignoring malformed screen context: %s", str(exc)[:200])
        return None


def _num(v: float) -> str:
    """Format a normalised coordinate compactly (``0.10`` → ``.10``).

    Args:
        v: A value in [0, 1].

    Returns:
        Two-decimal string without the leading zero (``1.00`` stays as is).
    """
    s = f"{v:.2f}"
    return s[1:] if s.startswith("0.") else s


def _quote(text: str) -> str:
    """Quote a UI label for the prompt, truncating and neutralising quotes.

    Args:
        text: Element name.

    Returns:
        The name wrapped in double quotes.
    """
    if len(text) > _PROMPT_NAME_CHARS:
        text = text[: _PROMPT_NAME_CHARS - 1] + "…"
    return '"' + text.replace('"', "'") + '"'


def render_context(context: ScreenContext) -> str:
    """Render the screen context as compact text for the user message.

    Args:
        context: The parsed screen context.

    Returns:
        Multi-line text: app / window / cursor facts, then one line per
        enabled element, in the order the client sent them.
    """
    lines: list[str] = ["Screen context (coordinates normalised 0-1, origin top-left):"]
    if context.app:
        lines.append(f"App: {context.app}")
    if context.window_title:
        lines.append(f"Window title: {_quote(context.window_title)}")
    if context.window:
        w = context.window
        lines.append(f"Active window: @{_num(w.x)},{_num(w.y)} {_num(w.w)}x{_num(w.h)}")
    if context.cursor:
        lines.append(f"Cursor: {_num(context.cursor.x)},{_num(context.cursor.y)}")

    if context.elements:
        enabled = [el for el in context.elements if el.enabled]
        disabled = len(context.elements) - len(enabled)
        header = "Marked elements ([id] role \"name\" @x,y wxh)"
        if disabled:
            header += f"; {disabled} disabled element(s) omitted"
        lines.append(header + ":")
        for el in enabled:
            parts = [f"[{el.id}]", el.role or "element"]
            if el.name:
                parts.append(_quote(el.name))
            parts.append(f"@{_num(el.x)},{_num(el.y)} {_num(el.w)}x{_num(el.h)}")
            if el.focused:
                parts.append("(focused)")
            lines.append(" ".join(parts))
    else:
        lines.append("No marked elements were provided for this screen.")
    return "\n".join(lines)


def _coerce_id(value: Any) -> int | None:
    """Interpret a model-emitted element id.

    Args:
        value: ``12``, ``"12"``, ``"#12"``, ``"[12]"`` …

    Returns:
        The integer id, or ``None`` if it isn't one.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().strip("[]#").strip()
        if cleaned.isdigit():
            return int(cleaned)
    return None


def _bbox_target(raw: Any) -> StepTarget | None:
    """Validate a fallback bbox from the model.

    Args:
        raw: ``[x, y, w, h]`` or ``{"x":…, "y":…, "w":…, "h":…}``.

    Returns:
        A clipped ``StepTarget`` with ``element_id=None``, or ``None`` if the
        box is malformed, not normalised, or degenerate.
    """
    if isinstance(raw, dict):
        raw = [raw.get("x"), raw.get("y"), raw.get("w"), raw.get("h")]
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        return None
    lo, hi = -_BBOX_TOLERANCE, 1.0 + _BBOX_TOLERANCE
    if not all(lo <= v <= hi for v in (x, y, x + w, y + h)):
        return None  # pixels or garbage, not normalised coordinates
    if w <= 0 or h <= 0 or w > _MAX_BBOX_SIDE or h > _MAX_BBOX_SIDE:
        return None
    x0, y0 = min(1.0, max(0.0, x)), min(1.0, max(0.0, y))
    x1, y1 = min(1.0, max(0.0, x + w)), min(1.0, max(0.0, y + h))
    if x1 <= x0 or y1 <= y0:
        return None
    return StepTarget(element_id=None, x=x0, y=y0, w=x1 - x0, h=y1 - y0)


def resolve_target(raw: Any, context: ScreenContext | None) -> StepTarget | None:
    """Turn one raw model target into a trusted ``StepTarget``.

    Args:
        raw: The step's ``target`` value from the model JSON.
        context: The request's screen context, if any.

    Returns:
        The resolved target, or ``None`` (see module docstring for rules).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        # Bare id ("target": 12) — accept it as an element id.
        raw = {"element_id": raw}

    raw_id = raw.get("element_id", raw.get("id"))
    if raw_id is not None:
        element_id = _coerce_id(raw_id)
        el = context.element(element_id) if (context and element_id is not None) else None
        if el is None:
            logger.info("Grounding: model chose unknown element id %r → no target", raw_id)
            return None
        return StepTarget(element_id=el.id, x=el.x, y=el.y, w=el.w, h=el.h)

    bbox = raw.get("bbox")
    if bbox is None and all(k in raw for k in ("x", "y", "w", "h")):
        bbox = raw
    if bbox is None:
        return None
    target = _bbox_target(bbox)
    if target is None:
        logger.info("Grounding: rejected invalid bbox %r → no target", bbox)
    return target


def resolve_targets(
    steps: list[ModelStep], context: ScreenContext | None
) -> list[BeaconStep]:
    """Resolve every step's raw model target against the screen context.

    Args:
        steps: Steps as parsed from the model, with raw ``target`` values.
        context: The request's screen context (``None`` for old clients).

    Returns:
        ``BeaconStep``s with trusted ``target`` boxes (or ``None``).
    """
    return [
        BeaconStep(
            step=s.step,
            instruction=s.instruction,
            keys=s.keys,
            tip=s.tip,
            target=resolve_target(s.target, context),
        )
        for s in steps
    ]
