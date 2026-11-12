"""Regression tests for the agent reply parsing (B2, B11).

The old ``_extract_json`` stripped leading zeros anywhere in the text, so
``0.08`` became ``0.8`` and ``"0803"`` became ``"803"`` — every small
coordinate was off by 10x.
"""

import json
from types import SimpleNamespace

import pytest

from app.services import agent as agent_service
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


# ── B11: strict validation + retry on unusable replies ───────────────────────

@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("false", False), ("TRUE", True), (None, False), (0, False)],
)
def test_goal_achieved_is_coerced_strictly(value, expected):
    """``"false"`` is False (``bool("false")`` would be True)."""
    assert agent_service._coerce_bool(value) is expected


@pytest.mark.parametrize("value", ["yes", 2, [], {}])
def test_goal_achieved_rejects_non_booleans(value):
    """Ambiguous flags are a parse error, not a guess."""
    with pytest.raises(ValueError):
        agent_service._coerce_bool(value)


@pytest.mark.parametrize(
    "patch",
    [{"x": "left"}, {"y": [1]}, {"action": "teleport"}, {"x": True}],
)
def test_bad_fields_raise_value_error(patch):
    """Bad coordinates / unknown actions raise ValueError (retryable), not TypeError."""
    with pytest.raises(ValueError):
        agent_service._parse_turn(json.dumps({**_PAYLOAD, **patch}))


def test_null_coordinates_use_defaults():
    """``null`` coordinates fall back to defaults instead of ``float(None)``."""
    _, action, done = agent_service._parse_turn(json.dumps({**_PAYLOAD, "w": None, "h": None}))
    assert (action.w, action.h) == (0.05, 0.05)
    assert done is False


class _FakeCompletions:
    """Returns queued replies from ``create``."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls = 0

    async def create(self, **_kwargs):  # noqa: ANN003
        self.calls += 1
        content = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture()
def agent_env(monkeypatch):
    """Patch the LLM and TTS; return a factory for a registered session."""
    async def _no_tts(text, voice):  # noqa: ANN001, ARG001
        return ""

    monkeypatch.setattr(agent_service, "_safe_tts", _no_tts)

    def _make(replies: list[str]):
        fake = _FakeCompletions(replies)
        monkeypatch.setattr(agent_service.groq_client.chat, "completions", fake)
        session = agent_service._Session("sess-b11", "goal", "en")
        session.messages = [{"role": "system", "content": "sys"}]
        agent_service._sessions[session.session_id] = session
        return session, fake

    yield _make
    agent_service._sessions.pop("sess-b11", None)


async def test_invalid_reply_is_retried_once(agent_env):
    """An unknown action triggers one corrective retry, then succeeds."""
    session, fake = agent_env(
        [json.dumps({**_PAYLOAD, "action": "teleport"}), json.dumps(_PAYLOAD)]
    )
    resp = await agent_service._run_turn(session, "voice")
    assert fake.calls == 2
    assert resp.done is False and resp.action is not None
    assert resp.action.x == 0.08


async def test_repeated_invalid_reply_ends_and_drops_session(agent_env):
    """Two unusable replies → done with a readable summary, session removed."""
    session, _ = agent_env(["not json", '{"action": "click"}'])
    resp = await agent_service._run_turn(session, "voice")
    assert resp.done is True and resp.action is None
    assert resp.summary == "Could not parse model response."
    assert session.session_id not in agent_service._sessions
