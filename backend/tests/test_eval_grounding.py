"""Tests for the grounding eval harness's scoring logic (no model calls)."""

import pytest

from app.schemas.vision import BeaconStep, StepTarget
from scripts.eval_grounding import DEFAULT_CASES_DIR, Expectation, load_case, load_cases, score_case

_FIXTURE = DEFAULT_CASES_DIR / "synthetic_save_dialog"


def _step(n: int, target: StepTarget | None) -> BeaconStep:
    """Build a predicted step."""
    return BeaconStep(step=n, instruction=f"Step {n}.", target=target)


def _element_target(case, element_id: int) -> StepTarget:
    """A target resolved from a context element, as the pipeline would produce."""
    el = case.context.element(element_id)
    return StepTarget(element_id=el.id, x=el.x, y=el.y, w=el.w, h=el.h)


class TestFixture:
    """The committed synthetic fixture loads."""

    def test_loads(self):
        """screen.jpg, context.json and case.json are read and parsed."""
        case = load_case(_FIXTURE)
        assert case.screenshot[:2] == b"\xff\xd8"  # JPEG
        assert case.context is not None
        assert [e.name for e in case.context.elements] == ["Save", "Don't Save", "Cancel"]
        assert case.request and case.language == "en"
        assert len(case.expected) == 1 and case.expected[0].step is None
        assert any(c.name == "synthetic_save_dialog" for c in load_cases(DEFAULT_CASES_DIR))


class TestScoreCase:
    """``score_case`` against the synthetic fixture."""

    @pytest.fixture()
    def case(self):
        """The loaded fixture."""
        return load_case(_FIXTURE)

    def test_any_step_element_hit(self, case):
        """The right element in any step → hit."""
        steps = [_step(1, None), _step(2, _element_target(case, 1))]
        score = score_case(case, steps)
        assert (score.hits, score.total) == (1, 1)

    def test_wrong_element_misses(self, case):
        """A different element → miss."""
        score = score_case(case, [_step(1, _element_target(case, 2))])
        assert score.hits == 0

    def test_bbox_containing_element_centre_hits(self, case):
        """A fallback bbox around the expected element's centre counts."""
        el = case.context.element(1)
        box = StepTarget(x=el.x - 0.01, y=el.y - 0.01, w=el.w + 0.02, h=el.h + 0.02)
        assert score_case(case, [_step(1, box)]).hits == 1

    def test_step_specific_and_point(self, case):
        """Step-bound expectations only look at that step; points need containment."""
        el = case.context.element(3)
        case.expected = [
            Expectation(step=1, element_id=1),
            Expectation(step=2, point=(el.x + el.w / 2, el.y + el.h / 2)),
        ]
        steps = [_step(1, _element_target(case, 3)), _step(2, _element_target(case, 3))]
        score = score_case(case, steps)
        assert [hit for _, hit in score.details] == [False, True]

    def test_no_targets(self, case):
        """No targets at all → zero hits."""
        assert score_case(case, [_step(1, None)]).hits == 0


class TestExpectationParsing:
    """``Expectation.from_dict`` validates labels."""

    def test_variants(self):
        """element_id / point / any_step forms parse."""
        assert Expectation.from_dict({"step": 2, "element_id": 5}) == Expectation(2, 5)
        assert Expectation.from_dict({"any_step": True, "point": [0.1, 0.2]}).point == (0.1, 0.2)

    @pytest.mark.parametrize("raw", [{"element_id": 1}, {"step": 1}])
    def test_invalid(self, raw):
        """Missing step or missing target → ValueError."""
        with pytest.raises(ValueError):
            Expectation.from_dict(raw)

