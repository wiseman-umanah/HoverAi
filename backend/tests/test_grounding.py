"""Tests for screen-context parsing, prompt rendering and target resolution.

Pure functions only — no LLM, no HTTP.
"""

import json

import pytest

from app.schemas.vision import MAX_CONTEXT_ELEMENTS, MAX_NAME_CHARS, ModelStep, ScreenContext
from app.services.grounding import (
    MAX_CONTEXT_BYTES,
    parse_screen_context,
    render_context,
    resolve_targets,
)


def _context(**overrides) -> dict:
    """Build a context dict like the desktop sends.

    Args:
        **overrides: Top-level keys to replace.

    Returns:
        The context as a plain dict.
    """
    ctx = {
        "app": "Code",
        "window_title": "main.rs — HoverAi",
        "window": {"x": 0.0, "y": 0.03, "w": 1.0, "h": 0.97},
        "cursor": {"x": 0.41, "y": 0.62},
        "elements": [
            {"id": 12, "role": "button", "name": "Save", "x": 0.10, "y": 0.20,
             "w": 0.05, "h": 0.03, "enabled": True},
            {"id": 13, "role": "Button", "name": "Delete", "x": 0.20, "y": 0.20,
             "w": 0.05, "h": 0.03, "enabled": False},
            {"id": 14, "role": "edit", "name": "", "x": 0.30, "y": 0.40,
             "w": 0.30, "h": 0.04, "focused": True},
        ],
    }
    ctx.update(overrides)
    return ctx


def _steps(*targets) -> list[ModelStep]:
    """Build model steps with the given raw targets.

    Args:
        *targets: One raw ``target`` value per step.

    Returns:
        ``ModelStep`` list numbered from 1.
    """
    return [
        ModelStep(step=i, instruction=f"Step {i}.", target=t)
        for i, t in enumerate(targets, start=1)
    ]


class TestParseScreenContext:
    """``parse_screen_context`` validates leniently and never raises."""

    def test_valid_context(self):
        """A well-formed context parses with all fields."""
        ctx = parse_screen_context(json.dumps(_context()))
        assert ctx is not None
        assert ctx.app == "Code"
        assert ctx.cursor is not None and ctx.cursor.x == pytest.approx(0.41)
        assert [e.id for e in ctx.elements] == [12, 13, 14]
        assert ctx.elements[1].role == "button"  # lower-cased
        assert ctx.elements[1].enabled is False
        assert [e.focused for e in ctx.elements] == [False, False, True]  # default false

    @pytest.mark.parametrize("raw", ["", "   ", None, "{not json", "[1, 2]", '"str"'])
    def test_absent_or_malformed_is_none(self, raw):
        """Blank, non-JSON or non-object context → None."""
        assert parse_screen_context(raw) is None

    def test_oversized_is_ignored(self):
        """A context over 200 KB is ignored."""
        big = json.dumps(_context(window_title="x" * (MAX_CONTEXT_BYTES + 1)))
        assert parse_screen_context(big) is None

    def test_coordinates_are_clipped(self):
        """Out-of-range values are clipped into the unit square."""
        ctx = parse_screen_context(json.dumps(_context(
            cursor={"x": 1.7, "y": -0.2},
            elements=[{"id": 1, "x": 0.95, "y": -0.1, "w": 0.2, "h": 0.05}],
        )))
        assert ctx is not None
        assert (ctx.cursor.x, ctx.cursor.y) == (1.0, 0.0)  # type: ignore[union-attr]
        el = ctx.elements[0]
        assert el.x == 0.95 and el.y == 0.0
        assert el.w == pytest.approx(0.05)  # x + w clipped to 1

    def test_bad_elements_are_dropped_not_fatal(self):
        """Invalid, zero-area and duplicate-id elements are dropped individually."""
        ctx = parse_screen_context(json.dumps(_context(elements=[
            {"id": 1, "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1},
            {"id": "nope", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1},
            {"id": 2, "x": 0.1},
            {"id": 3, "x": 1.0, "y": 0.1, "w": 0.1, "h": 0.1},   # zero width after clip
            {"id": 1, "x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1},   # duplicate id
            {"id": 4, "x": 0.2, "y": 0.2, "w": 0.1, "h": 0.1},
        ])))
        assert ctx is not None
        assert [e.id for e in ctx.elements] == [1, 4]

    def test_invalid_cursor_is_dropped(self):
        """A malformed cursor/window doesn't discard the rest of the context."""
        ctx = parse_screen_context(json.dumps(_context(cursor={"x": "left"}, window=None)))
        assert ctx is not None
        assert ctx.cursor is None and ctx.window is None
        assert len(ctx.elements) == 3

    def test_elements_capped_and_names_truncated(self):
        """At most 200 elements are kept; names are truncated."""
        els = [
            {"id": i, "role": "button", "name": "N" * 500, "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1}
            for i in range(300)
        ]
        ctx = parse_screen_context(json.dumps(_context(elements=els)))
        assert ctx is not None
        assert len(ctx.elements) == MAX_CONTEXT_ELEMENTS
        assert len(ctx.elements[0].name) == MAX_NAME_CHARS


class TestRenderContext:
    """``render_context`` produces the compact element list for the prompt."""

    def test_lists_enabled_elements_with_stable_ids(self):
        """Enabled elements are listed with their own ids; disabled are omitted."""
        ctx = ScreenContext.model_validate(_context())
        text = render_context(ctx)
        assert "App: Code" in text
        assert 'Window title: "main.rs — HoverAi"' in text
        assert "Cursor: .41,.62" in text
        assert '[12] button "Save" @.10,.20 .05x.03' in text
        assert "[14] edit @.30,.40 .30x.04 (focused)" in text
        assert '[12] button "Save" @.10,.20 .05x.03\n' in text  # not focused
        assert "[13]" not in text
        assert "1 disabled element(s) omitted" in text

    def test_no_elements(self):
        """A context without elements says so."""
        text = render_context(ScreenContext(app="Finder"))
        assert "App: Finder" in text
        assert "No marked elements" in text


class TestResolveTargets:
    """``resolve_targets`` trusts element bounds, validates fallback boxes."""

    @pytest.fixture()
    def ctx(self) -> ScreenContext:
        """The sample context."""
        return ScreenContext.model_validate(_context())

    def test_valid_id_uses_request_geometry(self, ctx):
        """A known id gets the element's bounds, ignoring model coordinates."""
        [step] = resolve_targets(
            _steps({"element_id": 12, "bbox": [0.9, 0.9, 0.05, 0.05]}), ctx
        )
        assert step.target is not None
        assert step.target.element_id == 12
        assert (step.target.x, step.target.y, step.target.w, step.target.h) == pytest.approx(
            (0.10, 0.20, 0.05, 0.03)
        )

    @pytest.mark.parametrize("raw_id", ["12", "#12", "[12]", 12.0])
    def test_id_variants_are_accepted(self, ctx, raw_id):
        """Common model spellings of an id resolve."""
        [step] = resolve_targets(_steps({"element_id": raw_id}), ctx)
        assert step.target is not None and step.target.element_id == 12

    def test_bare_int_target(self, ctx):
        """``"target": 14`` is treated as an element id."""
        [step] = resolve_targets(_steps(14), ctx)
        assert step.target is not None and step.target.element_id == 14

    @pytest.mark.parametrize("raw_id", [99, "Save", True, [12]])
    def test_unknown_or_invalid_id_is_none(self, ctx, raw_id):
        """Unknown/invalid ids → no target, even if a bbox is also given."""
        [step] = resolve_targets(
            _steps({"element_id": raw_id, "bbox": [0.1, 0.1, 0.1, 0.1]}), ctx
        )
        assert step.target is None

    def test_bbox_fallback_is_clamped(self, ctx):
        """A bbox without id is kept (element_id None) and clipped to [0, 1]."""
        [step] = resolve_targets(_steps({"bbox": [-0.02, 0.95, 0.10, 0.08]}), ctx)
        assert step.target is not None
        assert step.target.element_id is None
        assert step.target.x == 0.0 and step.target.y == pytest.approx(0.95)
        assert step.target.w == pytest.approx(0.08)
        assert step.target.h == pytest.approx(0.05)

    def test_bbox_as_object(self, ctx):
        """A bbox given as {x, y, w, h} works too."""
        [step] = resolve_targets(_steps({"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}), ctx)
        assert step.target is not None and step.target.w == pytest.approx(0.1)

    @pytest.mark.parametrize(
        "bbox",
        [
            [0.1, 0.1, 0.0, 0.1],      # zero width
            [0.1, 0.1, 0.1, -0.1],     # negative height
            [0.0, 0.0, 0.95, 0.5],     # too wide
            [0.05, 0.05, 0.5, 0.92],   # too tall
            [120, 300, 40, 20],        # pixels, not normalised
            [0.1, 0.1, 0.1],           # wrong arity
            ["a", 0.1, 0.1, 0.1],      # not numbers
            "0.1,0.1,0.1,0.1",         # not a list
        ],
    )
    def test_degenerate_bbox_is_none(self, ctx, bbox):
        """Degenerate or malformed boxes → no target."""
        [step] = resolve_targets(_steps({"bbox": bbox}), ctx)
        assert step.target is None

    def test_null_and_empty_targets(self, ctx):
        """``null`` and ``{}`` targets → None."""
        steps = resolve_targets(_steps(None, {}, {"element_id": None, "bbox": None}), ctx)
        assert [s.target for s in steps] == [None, None, None]

    def test_no_context(self):
        """Without context: ids can't resolve, bboxes still work."""
        steps = resolve_targets(
            _steps({"element_id": 12}, {"bbox": [0.1, 0.2, 0.05, 0.03]}), None
        )
        assert steps[0].target is None
        assert steps[1].target is not None and steps[1].target.element_id is None

    def test_step_fields_preserved(self, ctx):
        """Non-target fields pass through unchanged."""
        [step] = resolve_targets(
            [ModelStep(step=3, instruction="Press save.", keys="Ctrl+S", tip="t")], ctx
        )
        assert (step.step, step.instruction, step.keys, step.tip) == (3, "Press save.", "Ctrl+S", "t")
