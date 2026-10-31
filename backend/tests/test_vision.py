"""Tests for the vision pipeline — parsing, grounded prompts, JSON mode.

The Groq client is replaced by an in-process fake; nothing hits the network.
"""

import json

import httpx
import openai
import pytest

from app.schemas.vision import BeaconStep, ScreenContext, VisionResult
from app.services import vision as vision_service
from app.services.vision import VisionParseError, _parse_vision_response


class TestParseVisionResponse:
    """Unit tests for _parse_vision_response — no network calls needed."""

    def test_valid_json_returns_vision_result(self):
        """Valid JSON matching the schema returns a VisionResult."""
        raw = json.dumps({
            "summary": "Here is how to sum those rows.",
            "steps": [
                {"step": 1, "instruction": "Click on the target cell.", "keys": None, "tip": None}
            ],
        })
        result = _parse_vision_response(raw)
        assert isinstance(result, VisionResult)
        assert len(result.steps) == 1
        assert result.summary == "Here is how to sum those rows."

    def test_strips_markdown_fences(self):
        """JSON wrapped in ```json fences is parsed correctly."""
        inner = json.dumps({
            "summary": "Open menu",
            "steps": [
                {"step": 1, "instruction": "Click the File menu.", "keys": None, "tip": None}
            ],
        })
        raw = f"```json\n{inner}\n```"
        result = _parse_vision_response(raw)
        assert result.steps[0].instruction == "Click the File menu."

    def test_strips_plain_fences(self):
        """JSON wrapped in plain ``` fences (no language tag) is also parsed."""
        inner = json.dumps({
            "summary": "s",
            "steps": [
                {"step": 1, "instruction": "Tap the button.", "keys": None, "tip": None}
            ],
        })
        raw = f"```\n{inner}\n```"
        result = _parse_vision_response(raw)
        assert result.steps[0].instruction == "Tap the button."

    def test_invalid_json_raises_vision_parse_error(self):
        """Non-JSON input raises VisionParseError."""
        with pytest.raises(VisionParseError):
            _parse_vision_response("this is not json at all")

    def test_empty_steps_allowed(self):
        """A response with an empty steps list is valid."""
        raw = json.dumps({"summary": "Nothing found", "steps": []})
        result = _parse_vision_response(raw)
        assert result.steps == []

    def test_keys_and_tip_optional(self):
        """BeaconStep accepts steps with and without keys/tip fields."""
        step_with = BeaconStep(step=1, instruction="Press save.", keys="Ctrl+S", tip="Works on all platforms.")
        assert step_with.keys == "Ctrl+S"
        assert step_with.tip == "Works on all platforms."

        step_without = BeaconStep(step=2, instruction="Click OK.")
        assert step_without.keys is None
        assert step_without.tip is None

    def test_multiple_steps_preserved(self):
        """All steps in a multi-step response are returned."""
        raw = json.dumps({
            "summary": "Two steps",
            "steps": [
                {"step": 1, "instruction": "First step.", "keys": None, "tip": None},
                {"step": 2, "instruction": "Second step.", "keys": "Ctrl+S", "tip": "Save your work."},
            ],
        })
        result = _parse_vision_response(raw)
        assert len(result.steps) == 2
        assert result.steps[1].step == 2
        assert result.steps[1].keys == "Ctrl+S"


# ── Phase 3: grounding, prompt construction, JSON mode ────────────────────────

_CONTEXT = ScreenContext.model_validate({
    "app": "Code",
    "window_title": "main.rs — HoverAi",
    "cursor": {"x": 0.41, "y": 0.62},
    "elements": [
        {"id": 12, "role": "button", "name": "Save", "x": 0.10, "y": 0.20, "w": 0.05, "h": 0.03},
        {"id": 13, "role": "menuitem", "name": "File", "x": 0.01, "y": 0.0, "w": 0.03, "h": 0.02},
    ],
})

_MODEL_JSON = json.dumps({
    "summary": "Let me show you how to save.",
    "steps": [
        {"step": 1, "instruction": "Click Save.", "keys": None, "tip": None,
         "target": {"element_id": 12, "bbox": [0.5, 0.5, 0.1, 0.1]}},
        {"step": 2, "instruction": "Press Enter.", "keys": "Enter", "tip": None, "target": None},
        {"step": 3, "instruction": "Click the tab.", "keys": None, "tip": None,
         "target": {"bbox": [0.3, 0.3, 0.1, 0.05]}},
        {"step": 4, "instruction": "Click OK.", "keys": None, "tip": None,
         "target": {"element_id": 77}},
    ],
})


class _FakeCompletions:
    """Stands in for ``groq_client.chat.completions`` and records calls."""

    def __init__(self, responses: list) -> None:
        """Queue responses: strings are returned as content, exceptions raised."""
        self.responses = responses
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        """Return or raise the next queued response."""
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item

        class _Usage:
            prompt_tokens = 1000
            completion_tokens = 200
            total_tokens = 1200

        class _Msg:
            content = item

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()

        return _Resp()


def _bad_request(message: str, body: dict) -> openai.BadRequestError:
    """Build an ``openai.BadRequestError`` like Groq returns.

    Args:
        message: Error message.
        body: The ``error`` object.

    Returns:
        The exception instance.
    """
    return openai.BadRequestError(
        message,
        response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
        body=body,
    )


@pytest.fixture()
def fake_groq(monkeypatch):
    """Install a fake completions endpoint; returns a factory taking responses."""

    def install(responses: list) -> _FakeCompletions:
        fake = _FakeCompletions(responses)
        monkeypatch.setattr(vision_service.groq_client.chat, "completions", fake)
        monkeypatch.setattr(vision_service, "_JSON_MODE_UNSUPPORTED", set())
        monkeypatch.setattr(vision_service.settings, "vision_json_mode", True)
        return fake

    return install


def _user_text(call: dict) -> str:
    """Return the text block of the last user message of a recorded call."""
    content = [m for m in call["messages"] if m["role"] == "user"][0]["content"]
    return next(part["text"] for part in content if part["type"] == "text")


class TestGroundedVision:
    """``_groq_vision`` with a screen context, via a fake Groq client."""

    async def test_targets_resolved_and_usage_reported(self, fake_groq):
        """Element ids resolve to request geometry; unknown ids → None."""
        fake = fake_groq([_MODEL_JSON])
        result = await vision_service._groq_vision(
            "data:image/jpeg;base64,AAAA", "save my file", "en", context=_CONTEXT
        )
        targets = [s.target for s in result.steps]
        assert targets[0] is not None and targets[0].element_id == 12
        assert (targets[0].x, targets[0].y) == pytest.approx((0.10, 0.20))
        assert targets[1] is None
        assert targets[2] is not None and targets[2].element_id is None
        assert targets[3] is None
        assert result.usage == {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}
        assert fake.calls[0]["response_format"] == {"type": "json_object"}
        assert fake.calls[0]["max_tokens"] == vision_service._MAX_TOKENS

    async def test_prompt_includes_marks_and_element_list(self, fake_groq):
        """The system prompt explains marks; the user text lists elements."""
        fake = fake_groq([_MODEL_JSON])
        await vision_service._groq_vision(
            "data:image/jpeg;base64,AAAA", "save my file", "yo", context=_CONTEXT
        )
        call = fake.calls[0]
        system = call["messages"][0]["content"]
        assert "numbered marks" in system
        assert '"element_id": 12' in system
        assert "Yoruba" in system  # language instructions intact
        text = _user_text(call)
        assert "save my file" in text
        assert "App: Code" in text
        assert '[12] button "Save" @.10,.20 .05x.03' in text
        assert '[13] menuitem "File"' in text

    async def test_no_context_asks_for_bbox_only(self, fake_groq):
        """Old clients: no element list, bbox-only target rules."""
        fake = fake_groq([_MODEL_JSON])
        result = await vision_service._groq_vision(
            "data:image/jpeg;base64,AAAA", "save my file", "en"
        )
        system = fake.calls[0]["messages"][0]["content"]
        assert "numbered marks" not in system
        assert '"bbox"' in system
        assert "Screen context" not in _user_text(fake.calls[0])
        # ids can't resolve without context; the bbox step still gets a target
        assert result.steps[0].target is None
        assert result.steps[2].target is not None

    async def test_response_format_rejected_falls_back(self, fake_groq):
        """400 about response_format → one retry without it, remembered per model."""
        fake = fake_groq([
            _bad_request(
                "`response_format` json_object is not supported with this model",
                {"message": "response_format not supported", "type": "invalid_request_error"},
            ),
            _MODEL_JSON,
            _MODEL_JSON,
        ])
        result = await vision_service._groq_vision(
            "data:image/jpeg;base64,AAAA", "save", "en", context=_CONTEXT
        )
        assert len(result.steps) == 4
        assert "response_format" in fake.calls[0]
        assert "response_format" not in fake.calls[1]
        # The next request skips JSON mode straight away.
        await vision_service._groq_vision("data:image/jpeg;base64,AAAA", "save", "en")
        assert "response_format" not in fake.calls[2]

    async def test_json_mode_disabled_by_setting(self, fake_groq, monkeypatch):
        """vision_json_mode=False never sends response_format."""
        fake = fake_groq([_MODEL_JSON])
        monkeypatch.setattr(vision_service.settings, "vision_json_mode", False)
        await vision_service._groq_vision("data:image/jpeg;base64,AAAA", "save", "en")
        assert "response_format" not in fake.calls[0]

    async def test_json_validate_failed_salvages_generation(self, fake_groq):
        """Groq's json_validate_failed output is still run through the tolerant parser."""
        fake = fake_groq([
            _bad_request(
                "Failed to generate JSON",
                {"code": "json_validate_failed", "failed_generation": f"```json\n{_MODEL_JSON}\n```"},
            ),
        ])
        result = await vision_service._groq_vision(
            "data:image/jpeg;base64,AAAA", "save", "en", context=_CONTEXT
        )
        assert result.steps[0].target is not None
        assert len(fake.calls) == 1

    async def test_invalid_json_retries_once(self, fake_groq):
        """Unparseable output → one 'Invalid JSON' retry, then success."""
        fake = fake_groq(["sorry, no json here", _MODEL_JSON])
        result = await vision_service._groq_vision(
            "data:image/jpeg;base64,AAAA", "save", "en", context=_CONTEXT
        )
        assert len(result.steps) == 4
        assert fake.calls[1]["messages"][-1]["content"].startswith("Invalid JSON")

    async def test_other_bad_request_propagates(self, fake_groq):
        """An unrelated 400 is not swallowed."""
        fake_groq([_bad_request("context length exceeded", {"message": "too long"})])
        with pytest.raises(openai.BadRequestError):
            await vision_service._groq_vision("data:image/jpeg;base64,AAAA", "save", "en")
