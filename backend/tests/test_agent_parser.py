"""Regression tests for the agent JSON extraction (B2).

The old ``_extract_json`` stripped leading zeros anywhere in the text, so
``0.08`` became ``0.8`` and ``"0803"`` became ``"803"`` — every small
coordinate was off by 10x.
"""

import json

from app.services.agent import _extract_json, _parse_agent_response

_PAYLOAD = {
    "reasoning": "Chrome icon is top-left.",
    "action": "click",
    "instruction": "Click the Chrome icon",
    "x": 0.08,
    "y": 0.012,
    "w": 0.05,
    "h": 0.06,
    "text": "call 0803 at 10:05",
    "keys": None,
    "direction": None,
    "amount": None,
    "spoken_reply": "Opening Chrome!",
    "goal_achieved": False,
}


def test_coordinates_and_strings_are_not_rewritten():
    """Small fractions and digit strings survive extraction unchanged."""
    parsed = json.loads(_extract_json(json.dumps(_PAYLOAD)))
    assert parsed["x"] == 0.08
    assert parsed["y"] == 0.012
    assert parsed["w"] == 0.05
    assert parsed["text"] == "call 0803 at 10:05"


def test_fenced_and_think_wrapped_json_is_extracted_verbatim():
    """Fences, prose and <think> blocks are stripped without touching the JSON."""
    raw = (
        "<think>the icon is at 0.08</think>\nHere you go:\n```json\n"
        + json.dumps(_PAYLOAD)
        + "\n```"
    )
    data = _parse_agent_response(raw)
    assert data["x"] == 0.08
    assert data["text"] == "call 0803 at 10:05"
