#!/usr/bin/env python3
"""Grounding eval harness — how often do step targets hit the right element?

Runs the real vision pipeline (``services.vision.run_vision``, so it needs
``GROQ_API_KEY`` and the rest of ``.env``) over a directory of recorded
cases and scores each step's resolved ``target`` against human labels.
It is a manual tool for choosing/tuning the vision model (EXECUTION_PLAN
Phase 3 exit criterion: ≥ 80 % of steps hit), never run by the test suite —
only the pure scoring helpers here are unit tested.

Case format (one folder per case, see ``eval/README.md``):

    screen.jpg    — the screenshot exactly as sent (set-of-marks drawn on it)
    context.json  — the ``context`` field the desktop sent (may be absent)
    case.json     — {"request": "...", "language": "en", "expected": [...]}

An ``expected`` entry is ``{"step": n, "element_id": id}`` or
``{"step": n, "point": [x, y]}``; ``"any_step": true`` instead of ``"step"``
means "some step targets this". An element expectation also counts as hit
when the step's fallback bbox (no id) contains the centre of that element.

Usage:
    cd backend/
    uv run eval-grounding                      # all cases in eval/cases
    uv run eval-grounding path/to/cases --model meta-llama/llama-4-scout-17b-16e-instruct
    uv run eval-grounding --limit 5 --sleep 10 # stay under free-tier rate limits
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas.vision import BeaconStep, ScreenContext, StepTarget
from app.services.grounding import parse_screen_context

_BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CASES_DIR = _BACKEND_DIR / "eval" / "cases"


@dataclass
class Expectation:
    """One labelled target.

    Attributes:
        step: Step number the target belongs to, or ``None`` for any step.
        element_id: Expected element mark id (mutually exclusive with ``point``).
        point: Normalised ``(x, y)`` that the predicted box must contain.
    """

    step: int | None
    element_id: int | None = None
    point: tuple[float, float] | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "Expectation":
        """Parse an ``expected`` entry from ``case.json``.

        Args:
            raw: The entry.

        Returns:
            The expectation.

        Raises:
            ValueError: If the entry has neither a step nor ``any_step``, or
                neither ``element_id`` nor ``point``.
        """
        step = None if raw.get("any_step") else raw.get("step")
        if step is None and not raw.get("any_step"):
            raise ValueError(f"expected entry needs 'step' or 'any_step': {raw}")
        if "element_id" in raw:
            return cls(step=int(step) if step is not None else None, element_id=int(raw["element_id"]))
        if "point" in raw:
            x, y = raw["point"]
            return cls(step=int(step) if step is not None else None, point=(float(x), float(y)))
        raise ValueError(f"expected entry needs 'element_id' or 'point': {raw}")

    def describe(self) -> str:
        """Short human-readable label, e.g. ``step 1 → #12``."""
        where = "any step" if self.step is None else f"step {self.step}"
        what = f"#{self.element_id}" if self.element_id is not None else f"point {self.point}"
        return f"{where} → {what}"


@dataclass
class Case:
    """A loaded eval case.

    Attributes:
        name: Folder name.
        screenshot: JPEG bytes.
        context: Parsed screen context (``None`` if absent or malformed).
        request: The user's request (transcript text).
        language: Hover AI language code.
        expected: Labelled targets (empty = unlabelled, not scored).
    """

    name: str
    screenshot: bytes
    context: ScreenContext | None
    request: str
    language: str
    expected: list[Expectation] = field(default_factory=list)


@dataclass
class CaseScore:
    """Scoring result for one case.

    Attributes:
        hits: Number of satisfied expectations.
        total: Number of expectations.
        details: One ``(expectation label, hit)`` pair per expectation.
    """

    hits: int
    total: int
    details: list[tuple[str, bool]]


def load_case(case_dir: Path) -> Case:
    """Load a case folder.

    Args:
        case_dir: Folder with ``screen.jpg``, ``case.json`` and optionally
            ``context.json``.

    Returns:
        The loaded case.

    Raises:
        FileNotFoundError: If ``screen.jpg`` or ``case.json`` is missing.
        ValueError: If ``case.json`` is malformed.
    """
    meta = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    context_path = case_dir / "context.json"
    context = (
        parse_screen_context(context_path.read_text(encoding="utf-8"))
        if context_path.exists()
        else None
    )
    return Case(
        name=case_dir.name,
        screenshot=(case_dir / "screen.jpg").read_bytes(),
        context=context,
        request=str(meta["request"]),
        language=str(meta.get("language") or "en"),
        expected=[Expectation.from_dict(e) for e in meta.get("expected", [])],
    )


def load_cases(cases_dir: Path) -> list[Case]:
    """Load every case folder (sorted by name) under ``cases_dir``.

    Args:
        cases_dir: Directory containing one folder per case.

    Returns:
        The loaded cases.
    """
    return [
        load_case(d) for d in sorted(cases_dir.iterdir())
        if d.is_dir() and (d / "case.json").exists()
    ]


def _contains(target: StepTarget, x: float, y: float) -> bool:
    """Whether a target box contains a point (edges inclusive).

    Args:
        target: The predicted box.
        x: Point x (normalised).
        y: Point y (normalised).

    Returns:
        ``True`` if the point lies inside the box.
    """
    return target.x <= x <= target.x + target.w and target.y <= y <= target.y + target.h


def _target_hits(
    target: StepTarget | None, exp: Expectation, context: ScreenContext | None
) -> bool:
    """Whether one predicted target satisfies one expectation.

    Args:
        target: The step's resolved target.
        exp: The expectation.
        context: The case's screen context (for element centres).

    Returns:
        ``True`` on a hit.
    """
    if target is None:
        return False
    if exp.point is not None:
        return _contains(target, *exp.point)
    if target.element_id is not None:
        return target.element_id == exp.element_id
    el = context.element(exp.element_id) if (context and exp.element_id is not None) else None
    return el is not None and _contains(target, el.x + el.w / 2, el.y + el.h / 2)


def score_case(case: Case, steps: list[BeaconStep]) -> CaseScore:
    """Score predicted steps against a case's expectations.

    Args:
        case: The labelled case.
        steps: Steps returned by the vision pipeline (targets resolved).

    Returns:
        Hits, total and per-expectation details.
    """
    details: list[tuple[str, bool]] = []
    for exp in case.expected:
        candidates = steps if exp.step is None else [s for s in steps if s.step == exp.step]
        hit = any(_target_hits(s.target, exp, case.context) for s in candidates)
        details.append((exp.describe(), hit))
    return CaseScore(hits=sum(h for _, h in details), total=len(details), details=details)


def _fmt_target(target: StepTarget | None) -> str:
    """Compact target label for the report."""
    if target is None:
        return "-"
    if target.element_id is not None:
        return f"#{target.element_id}"
    return f"bbox({target.x:.2f},{target.y:.2f} {target.w:.2f}x{target.h:.2f})"


async def _run(cases: list[Case], sleep_s: float) -> int:
    """Run the vision pipeline over the cases and print the report.

    Args:
        cases: Cases to run.
        sleep_s: Pause between cases (rate limits).

    Returns:
        Process exit code (0, or 1 if any case errored).
    """
    from app.config import settings  # noqa: PLC0415 — needs .env; keep scoring import-light
    from app.services.vision import run_vision  # noqa: PLC0415

    print(f"Model: {settings.vision_model}  json_mode={settings.vision_json_mode}  "
          f"cases={len(cases)}\n")
    hits = total = errors = 0
    latencies: list[float] = []
    prompt_tokens = completion_tokens = 0
    for i, case in enumerate(cases):
        if i and sleep_s:
            await asyncio.sleep(sleep_s)
        n_el = len(case.context.elements) if case.context else 0
        start = time.perf_counter()
        try:
            result = await run_vision(
                case.screenshot, case.request, case.language, context=case.context
            )
        except Exception as exc:  # noqa: BLE001 — report and continue
            errors += 1
            total += len(case.expected)
            print(f"ERROR {case.name}: {type(exc).__name__}: {exc}\n")
            continue
        latency = time.perf_counter() - start
        latencies.append(latency)
        usage = result.usage or {}
        prompt_tokens += usage.get("prompt_tokens", 0)
        completion_tokens += usage.get("completion_tokens", 0)

        score = score_case(case, result.steps)
        hits += score.hits
        total += score.total
        status = "UNLABELLED" if not score.total else (
            "PASS" if score.hits == score.total else "MISS"
        )
        print(f"{status} {case.name}  {score.hits}/{score.total}  {latency:.1f}s  "
              f"elements={n_el}  tokens={usage.get('prompt_tokens', '?')}+"
              f"{usage.get('completion_tokens', '?')}")
        print(f"  request: {case.request}")
        for s in result.steps:
            print(f"  {s.step}. [{_fmt_target(s.target)}] {s.instruction}")
        for label, hit in score.details:
            print(f"  {'✓' if hit else '✗'} expected {label}")
        print()

    print("─" * 60)
    rate = f"{hits / total:.0%}" if total else "n/a"
    print(f"Hit rate: {hits}/{total} = {rate}  (errors: {errors})")
    if latencies:
        print(f"Latency: mean {statistics.mean(latencies):.1f}s, "
              f"median {statistics.median(latencies):.1f}s, max {max(latencies):.1f}s")
    if prompt_tokens or completion_tokens:
        print(f"Tokens: {prompt_tokens} prompt + {completion_tokens} completion "
              f"(mean {prompt_tokens // max(1, len(latencies))} prompt/case)")
    return 1 if errors else 0


def main() -> None:
    """Console entry point (``uv run eval-grounding``)."""
    parser = argparse.ArgumentParser(description="Score vision step targets on eval cases.")
    parser.add_argument("cases_dir", nargs="?", type=Path, default=DEFAULT_CASES_DIR,
                        help=f"directory of case folders (default: {DEFAULT_CASES_DIR})")
    parser.add_argument("--model", help="override VISION_MODEL for this run")
    parser.add_argument("--limit", type=int, help="run at most N cases")
    parser.add_argument("--sleep", type=float, default=0.0,
                        help="seconds to wait between cases (rate limits)")
    args = parser.parse_args()

    cases = load_cases(args.cases_dir)
    if args.limit is not None:
        cases = cases[: args.limit]
    if not cases:
        print(f"No cases found in {args.cases_dir}")
        sys.exit(1)
    if args.model:
        from app.config import settings  # noqa: PLC0415

        settings.vision_model = args.model
    sys.exit(asyncio.run(_run(cases, args.sleep)))


if __name__ == "__main__":
    main()
