# Grounding eval set

Measures how often the vision model's step targets land on the right UI element
(EXECUTION_PLAN Phase 3 exit criterion: at least 80 % of labelled steps hit). Use it to compare
vision models and prompt changes.

```bash
cd backend/
uv run eval-grounding                         # every case in eval/cases/
uv run eval-grounding path/to/cases --limit 5
uv run eval-grounding --model meta-llama/llama-4-scout-17b-16e-instruct --sleep 10
```

It calls the real pipeline (`services/vision.run_vision`), so it needs a filled-in `.env`
(`GROQ_API_KEY` etc.) and spends API quota. Use `--sleep` to stay under free-tier rate limits.
It is never run by `pytest`; only its scoring logic is unit-tested (`tests/test_eval_grounding.py`).

For each case it prints the predicted steps with their targets (`#12` = element id,
`bbox(...)` = fallback box, `-` = no target), which expectations hit, latency and token usage.
At the end it prints the overall hit rate, latency stats and token totals.

## Case format

One folder per case under `eval/cases/`:

```
eval/cases/<case-name>/
├── screen.jpg     # the screenshot exactly as the desktop sent it (set-of-marks drawn on it)
├── context.json   # the `context` field the desktop sent (optional)
└── case.json
```

`context.json` uses the `/query` `context` format: `app`, `window_title`, `window`, `cursor`,
`elements[]` with `id`, `role`, `name`, `x`, `y`, `w`, `h`, `enabled` and optionally
`focused`. All coordinates are
normalised 0–1 to the screenshot, with x/y the top-left corner.

`case.json`:

```json
{
  "request": "How do I save my changes?",
  "language": "en",
  "expected": [
    {"step": 1, "element_id": 12},
    {"step": 2, "point": [0.42, 0.18]},
    {"any_step": true, "element_id": 7}
  ]
}
```

An `expected` entry is one of:

- `{"step": n, "element_id": id}`: step `n` must target element `id`. A fallback bbox with no id
  also counts if it contains the centre of element `id`.
- `{"step": n, "point": [x, y]}`: step `n`'s target box must contain the point. Use this for
  things that have no element.
- Either form with `"any_step": true` instead of `"step"`: some step must hit it. This is useful
  when the number of steps before it can vary.

A case with `"expected": []` is unlabelled. It still runs and prints its predictions, which is
handy when labelling, but it isn't scored.

## Collecting cases from the desktop app

When the desktop app runs with `HOVERAI_FIXTURE_DIR=/some/dir`, it dumps every query as a case
folder: `screen.jpg`, `context.json`, and a `case.json` with `request` set to the transcript and
`"expected": []`. Open `screen.jpg` and fill in `expected` from the numbered marks, then move the
folder into `eval/cases/`. Strip anything private from screenshots before committing them.

## Fixtures in this repo

- `synthetic_save_dialog/` is a tiny dialog drawn with PIL, with three marked buttons. It's there
  to smoke-test the harness, not to measure the model.
