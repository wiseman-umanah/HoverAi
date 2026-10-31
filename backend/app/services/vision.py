"""Vision pipeline — transcript → beacon steps.

Uses Groq qwen/qwen3.8-27b (multimodal, free tier) via the OpenAI-compatible
API to identify UI elements from a screenshot + voice transcript.

The screenshot is compressed to 512×288 JPEG at 35% quality before sending.
This keeps the image token count to ~1,100–1,450, well under the 7,000 ITPM
free-tier limit. The image is passed as a proper vision content block
(type: image_url with a data: URI) — NOT as raw base64 text in the message
string, which would be read as characters rather than decoded as an image.

Previous failure mode: the image was embedded as plain text in the content
string — Qwen3 read the literal base64 characters, spending ~9,966 tokens
with zero visual understanding, which caused the 413 ITPM error.

Phase 3 grounding (AGENTIC_PLAN D2/D3):
  - When the desktop sends a ``ScreenContext`` with elements, the screenshot
    carries numbered set-of-marks labels. It is then compressed less
    aggressively (long edge ``settings.vision_marked_max_edge``) so the
    numbers stay legible, the prompt explains the marks, and the user
    message lists the elements compactly (``grounding.render_context``).
  - The model answers each step's ``target`` with ``{"element_id": N}``, a
    fallback ``{"bbox": [x, y, w, h]}``, or ``null``; ``resolve_targets``
    replaces that with server-trusted geometry. Without a context (old
    clients) only bbox targets are requested, best effort.
  - Output is requested in Groq JSON mode (``settings.vision_json_mode``).
    A 400 that rejects ``response_format`` triggers one retry without it
    (remembered per model); a ``json_validate_failed`` 400 is treated like
    unparseable output. The tolerant ``_extract_json`` parser and the single
    "Invalid JSON" retry remain the fallback. Full tool calling (D3) waits
    for the agent WebSocket work.
"""

import asyncio
import json
import logging
import re
from typing import Any

import openai

from app.config import settings
from app.schemas.vision import ModelStep, ScreenContext, VisionResult
from app.services.grounding import render_context, resolve_targets
from app.services.images import compress_to_jpeg_b64
from app.services.llm_client import groq_client

logger = logging.getLogger(__name__)

# Compression targets.
# We need to balance two competing constraints:
#   1. Stay under Groq's 7,000 ITPM free-tier limit.
#   2. Preserve enough detail for Qwen3 to pinpoint small UI elements (icons,
#      buttons) that may be 32–64 px on a 1920×1080 display.
#
# At 512×288 a desktop icon becomes a ~17×17 blob — too small for accurate
# centre estimation.  Bumping to 768×432 keeps each icon ~21×21 px which
# meaningfully improves hit rate.  Token cost rises to ~2,000–2,500 leaving
# ~4,500 tokens headroom — still comfortably under the 7k cap.
_MAX_WIDTH = 768
_MAX_HEIGHT = 432
_JPEG_QUALITY = 40
# Set-of-marks screenshots: mark numbers are ~12–16 px tall at 1600 px, so a
# 768 px copy makes them unreadable. Keep them larger and a bit sharper.
_MARKED_JPEG_QUALITY = 60

# Output budget: steps now carry targets, 512 truncated longer answers.
_MAX_TOKENS = 900

# Models whose provider rejected response_format (skip JSON mode until restart).
_JSON_MODE_UNSUPPORTED: set[str] = set()


def _compress_screenshot(image_bytes: bytes, marked: bool = False) -> str:
    """Resize and JPEG-compress a screenshot to reduce token count.

    CPU-bound — call through ``asyncio.to_thread`` from async code.

    Args:
        image_bytes: Raw PNG/JPEG bytes of the screenshot.
        marked: The screenshot carries set-of-marks labels — keep the long
            edge at ``settings.vision_marked_max_edge`` instead of 768×432.

    Returns:
        A plain base64 string (no data URI prefix) of the compressed JPEG.

    Raises:
        InvalidImageError: If the bytes are not a decodable image.
    """
    if marked:
        edge = settings.vision_marked_max_edge
        return compress_to_jpeg_b64(image_bytes, edge, edge, _MARKED_JPEG_QUALITY)
    return compress_to_jpeg_b64(image_bytes, _MAX_WIDTH, _MAX_HEIGHT, _JPEG_QUALITY)


# Per-language instructions injected into the system prompt.
# Key: Hover AI language code.  Value: (label, tone_instruction, summary_example)
#   label            — human-readable name shown to the model
#   tone_instruction — exact register/dialect the model must write in
#   summary_example  — a one-sentence example summary in that language/dialect
_LANGUAGE_CONFIGS: dict[str, tuple[str, str, str]] = {
    "en-pidgin": (
        "Nigerian Pidgin English",
        (
            "Write ALL text in warm, natural Nigerian Pidgin English — the way a helpful "
            "Lagos friend would explain things. Use pidgin vocabulary and rhythm: "
            "'You don click am', 'Abeg click here', 'E go work', 'Make you', 'I don see am', "
            "'Na so e dey work', 'Click the thing wey dey on top'. "
            "Mix in everyday English words for technical terms (Excel, button, menu) but "
            "keep the sentence structure and feel as pidgin. Never switch to formal English."
        ),
        "I don see your Excel spreadsheet! Make I show you how to do am.",
    ),
    "yo": (
        "Yoruba",
        (
            "Write ALL text in Yoruba. Use clear, natural Yoruba as a helpful friend would speak. "
            "Technical terms (Excel, button, menu, click) may remain in English since they have "
            "no standard Yoruba equivalents — but all other text must be in Yoruba."
        ),
        "Mo ti ri iwe-iṣiro Excel rẹ! Jẹ́ kí n ṣe àlàyé bí o ṣe lè ṣe é.",
    ),
    "ha": (
        "Hausa",
        (
            "Write ALL text in Hausa. Use clear, natural Hausa as a helpful friend would speak. "
            "Technical terms (Excel, button, menu, click) may remain in English since they have "
            "no standard Hausa equivalents — but all other text must be in Hausa."
        ),
        "Na ga takarda Excel ɗinka! Bari in nuna maka yadda za ka yi shi.",
    ),
    "ig": (
        "Igbo",
        (
            "Write ALL text in Igbo. Use clear, natural Igbo as a helpful friend would speak. "
            "Technical terms (Excel, button, menu, click) may remain in English since they have "
            "no standard Igbo equivalents — but all other text must be in Igbo."
        ),
        "Ahụrụ m ihe Excel gị! Ka m gọọ gị otú esi eme ya.",
    ),
    "fr": (
        "French",
        "Write ALL text in French. Use clear, warm, natural French.",
        "Je vois votre feuille Excel ! Voici comment procéder.",
    ),
    "en": (
        "English",
        "Write ALL text in clear, warm, natural English.",
        "I can see your Excel spreadsheet! Here's how to do it.",
    ),
    "af": (
        "Afrikaans",
        "Write ALL text in clear, natural Afrikaans. Technical terms (Excel, button, menu, click) may remain in English.",
        "Ek kan jou Excel-sigblad sien! Hier is hoe om dit te doen.",
    ),
    "am": (
        "Amharic",
        "Write ALL text in clear, natural Amharic. Technical terms (Excel, button, menu, click) may remain in English.",
        "የእርስዎን Excel ሰነድ አይቻለሁ! እንዴት ማድረግ እንደሚቻል ላሳይዎ።",
    ),
    "rw": (
        "Kinyarwanda",
        "Write ALL text in clear, natural Kinyarwanda. Technical terms may remain in English.",
        "Mbona inyandiko yawe ya Excel! Reka ngereranye uburyo bwo kubikora.",
    ),
    "lg": (
        "Luganda",
        "Write ALL text in clear, natural Luganda. Technical terms may remain in English.",
        "Nlaba ssitaamu lyo lya Excel! Ka nkulagirire engeri gy'okolera.",
    ),
    "om": (
        "Oromo",
        "Write ALL text in clear, natural Oromo. Technical terms may remain in English.",
        "Sanadii Excel kee arga! Akkaataa raawwachuu agarsiisaaf.",
    ),
    "sn": (
        "Shona",
        "Write ALL text in clear, natural Shona. Technical terms may remain in English.",
        "Ndinoona spreadsheet yako yeExcel! Rega ndikuratidze kuitora.",
    ),
    "sw": (
        "Swahili",
        "Write ALL text in clear, natural Swahili. Technical terms may remain in English.",
        "Naona lahajedwali lako la Excel! Hebu nikuonyeshe jinsi ya kuifanya.",
    ),
    "wo": (
        "Wolof",
        "Write ALL text in clear, natural Wolof. Technical terms may remain in English.",
        "Maa ngi gis sa feyil Excel bi! Dem na naa la jox jëfandikoo bi.",
    ),
    "zu": (
        "Zulu",
        "Write ALL text in clear, natural Zulu. Technical terms may remain in English.",
        "Ngibona iSpredishithi yakho ye-Excel! Ake ngikukhombise ukuthi yenziwa kanjani.",
    ),
}

_DEFAULT_LANGUAGE_CONFIG = _LANGUAGE_CONFIGS["en"]


_TARGET_RULES_MARKED = """TARGET RULES — the screenshot has numbered marks:
- Interactive elements on the screenshot are outlined and labelled with a number (a "mark").
  The user message lists them as [id] role "name" @x,y wxh (normalised 0-1, top-left origin);
  "(focused)" marks the element that currently has keyboard focus.
- For each step that clicks, selects or types into something visible right now, set
  "target": {"element_id": <mark number>}. Match by the label text and position in the list;
  use the number drawn on the screenshot when the list is ambiguous.
- If the thing to click is visible but has no mark, set "target": {"bbox": [x, y, w, h]}
  — a tight box in normalised 0-1 coordinates of the screenshot.
- Set "target": null for keyboard-only steps, waiting, and anything that is not on screen yet
  (e.g. a menu item that only appears after an earlier step opens the menu).
- Never invent mark numbers that are not in the list."""

_TARGET_RULES_UNMARKED = """TARGET RULES:
- For each step that clicks or types into something visible right now, set
  "target": {"bbox": [x, y, w, h]} — a tight box around it in normalised 0-1 coordinates
  of the screenshot (x, y = top-left corner).
- Set "target": null for keyboard-only steps, waiting, anything not on screen yet, or when
  you cannot locate it precisely."""


def _build_system_prompt(language: str, marked: bool = False) -> str:
    """Build the vision system prompt with a concrete language instruction.

    Replaces the vague "write in the user's language" rule with an explicit
    register description, tone guidance, and a concrete example sentence so
    the model knows exactly what dialect/register to write in.

    Args:
        language: Hover AI language code (e.g. ``"en-pidgin"``).
        marked: The screenshot has set-of-marks labels and the user message
            lists elements — ask for ``element_id`` targets. Otherwise only
            bbox targets are requested.

    Returns:
        Full system prompt string with language instruction injected.
    """
    label, tone_instruction, summary_example = _LANGUAGE_CONFIGS.get(
        language, _DEFAULT_LANGUAGE_CONFIG
    )
    if marked:
        target_rules = _TARGET_RULES_MARKED
        target_example = '{"element_id": 12}'
    else:
        target_rules = _TARGET_RULES_UNMARKED
        target_example = '{"bbox": [0.10, 0.20, 0.05, 0.03]}'

    return f"""You are Hover — a warm, knowledgeable AI assistant that guides \
users step-by-step through tasks on their computer.

You receive a screenshot of the user's screen and a voice instruction.
Your job is to look at what is on screen and give the user clear, numbered \
instructions they can follow themselves to complete the task.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSE FORMAT — raw JSON only, no markdown, no explanation, no <think> tags:
{{
  "summary": "{summary_example}",
  "steps": [
    {{
      "step": 1,
      "instruction": "...",
      "keys": null,
      "tip": null,
      "target": {target_example}
    }},
    {{
      "step": 2,
      "instruction": "...",
      "keys": "Enter",
      "tip": "...",
      "target": null
    }}
  ]
}}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LANGUAGE — {label}:
{tone_instruction}

STEP RULES:
- Write each instruction as a clear, complete sentence a non-technical user can follow.
- Be specific: name the exact button, menu item, cell, or key to use.
- Reference what you actually see on screen — if you can see the app name, menu labels,
  cell references, or button text, use those exact names.
- If a step involves a keyboard shortcut, put it in the "keys" field (e.g. "Ctrl+S").
- Use the "tip" field for extra context, warnings, or variations (keep it short).
- Break the task into as many steps as needed — do not skip steps or combine unrelated actions.
- Order steps exactly as they must be performed.

{target_rules}

STEP EXAMPLES by task type:
- "sum rows in Excel": click target cell → type =SUM(range) → Enter
- "open a file": File menu → Open → navigate to file → double-click it
- "save as PDF": File → Export / Save As → choose PDF format → Save
- "apply a filter": select header row → Data tab → Filter button
- "bold text in Word": select the text → Ctrl+B or click Bold button on toolbar
- "create a chart": select data range → Insert tab → Chart → choose type → OK

SUMMARY RULES — this is spoken aloud via TTS:
- Speak directly and warmly using the {label} tone above.
- Name the app and what you see on screen.
- Keep it to 1-2 sentences maximum.
- NEVER say "The user wants..." — always say "you" / "your".

Return ONLY the JSON object. No markdown. No preamble. No extra text. No <think> tags."""


class VisionParseError(Exception):
    """Raised when the vision LLM response cannot be parsed as valid JSON.

    Attributes:
        raw_response: The raw string returned by the model before parsing.
    """

    def __init__(self, raw_response: str) -> None:
        """Initialise with the unparseable model response.

        Args:
            raw_response: The raw model output that failed JSON parsing.
        """
        super().__init__(f"Vision model returned unparseable JSON: {raw_response[:200]}")
        self.raw_response = raw_response


def _strip_thinking(raw: str) -> str:
    """Strip Qwen3 chain-of-thought <think>...</think> blocks from output.

    Qwen3 sometimes emits a reasoning block before the JSON answer. This
    function removes it so the JSON parser only sees the final answer.

    Args:
        raw: Raw model output, possibly containing <think>...</think> tags.

    Returns:
        The raw string with any <think> block removed and whitespace stripped.
    """
    # Remove everything inside <think>...</think> (non-greedy, dotall)
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    return cleaned.strip()


def _extract_json(raw: str) -> str:
    """Extract the JSON object from a model response robustly.

    Handles: bare JSON, ```json fences, ``` fences, prose prefixes,
    and <think> blocks (Qwen3 chain-of-thought).

    Args:
        raw: Raw model output string.

    Returns:
        Extracted JSON string ready for ``json.loads``.
    """
    text = _strip_thinking(raw).strip()
    # Strip any markdown code fence
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Pull out first {...} block if response doesn't start with {
    if not text.startswith("{"):
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    return text


def _parse_vision_response(raw: str, context: ScreenContext | None = None) -> VisionResult:
    """Parse the raw model response string into a VisionResult.

    Step targets are resolved against ``context`` (``resolve_targets``): a
    bad target never fails the parse, it just becomes ``None``.

    Args:
        raw: Raw string output from the vision LLM.
        context: The request's screen context, if any.

    Returns:
        A validated ``VisionResult`` instance.

    Raises:
        VisionParseError: If the string is not valid JSON or does not
            match the expected schema.
    """
    try:
        data = json.loads(_extract_json(raw))
        model_steps = [ModelStep(**s) for s in data.get("steps", [])]
        summary = data.get("summary", "")
        if not isinstance(summary, str):
            raise TypeError("summary must be a string")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise VisionParseError(raw) from exc
    return VisionResult(steps=resolve_targets(model_steps, context), summary=summary)


def _build_user_text(transcript: str, language: str, context: ScreenContext | None) -> str:
    """Build the text block of the current user turn.

    Args:
        transcript: User's voice instruction.
        language: Hover AI language code.
        context: Screen context from the desktop, if any.

    Returns:
        The voice instruction, followed by the rendered screen context when
        present, and the closing request for JSON.
    """
    parts = [f"Voice instruction (language: {language}):\n{transcript}"]
    if context is not None:
        parts.append(render_context(context))
    parts.append(
        "Look at the screenshot and give the user step-by-step instructions "
        "to complete their request, with a target for each step. Return JSON."
    )
    return "\n\n".join(parts)


def _is_json_validate_failure(exc: openai.BadRequestError) -> bool:
    """Whether Groq rejected JSON-mode output as invalid JSON.

    Args:
        exc: The 400 error from the provider.

    Returns:
        ``True`` for Groq's ``json_validate_failed`` error.
    """
    return getattr(exc, "code", None) == "json_validate_failed" or (
        "json_validate_failed" in str(exc)
    )


def _rejects_response_format(exc: openai.BadRequestError) -> bool:
    """Whether a 400 says the model does not support ``response_format``.

    Args:
        exc: The 400 error from the provider.

    Returns:
        ``True`` if the error message is about ``response_format`` / JSON mode.
    """
    msg = str(exc).lower()
    return "response_format" in msg or "json_object" in msg or "json mode" in msg


def _failed_generation(exc: openai.BadRequestError) -> str:
    """Extract the model's rejected output from a ``json_validate_failed`` 400.

    Args:
        exc: The 400 error from the provider.

    Returns:
        Groq's ``failed_generation`` text, or ``""`` if absent.
    """
    body = exc.body
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict):
            failed = inner.get("failed_generation")
            if isinstance(failed, str):
                return failed
    return ""


def _usage_dict(resp: Any) -> dict[str, int] | None:
    """Pull token usage out of a chat completion response.

    Args:
        resp: The ``ChatCompletion`` object.

    Returns:
        ``{"prompt_tokens", "completion_tokens", "total_tokens"}`` or ``None``.
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None
    out = {
        k: v
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(v := getattr(usage, k, None), int)
    }
    return out or None


async def _create_completion(
    messages: list[dict], json_mode: bool
) -> tuple[str, dict[str, int] | None]:
    """One vision chat completion, in JSON mode when enabled and supported.

    If the provider answers 400 because the model doesn't support
    ``response_format``, the model is remembered in ``_JSON_MODE_UNSUPPORTED``
    and the call is repeated once without it. A ``json_validate_failed``
    400 returns Groq's ``failed_generation`` text so the tolerant parser
    still gets a chance at it.

    Args:
        messages: Chat messages.
        json_mode: Request ``response_format={"type": "json_object"}``.

    Returns:
        ``(raw content, token usage or None)``.

    Raises:
        openai.APIError: Any other provider failure.
    """
    model = settings.vision_model
    if json_mode and model not in _JSON_MODE_UNSUPPORTED:
        try:
            resp = await groq_client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0.1,
                max_tokens=_MAX_TOKENS,
                response_format={"type": "json_object"},
            )
            return resp.choices[0].message.content or "", _usage_dict(resp)
        except openai.BadRequestError as exc:
            if _is_json_validate_failure(exc):
                logger.warning("Groq JSON mode rejected the model output as invalid JSON")
                return _failed_generation(exc), None
            if not _rejects_response_format(exc):
                raise
            logger.warning(
                "Model %s rejected response_format (%s) — retrying without JSON mode",
                model, str(exc)[:200],
            )
            _JSON_MODE_UNSUPPORTED.add(model)

    resp = await groq_client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=0.1,
        max_tokens=_MAX_TOKENS,
    )
    return resp.choices[0].message.content or "", _usage_dict(resp)


async def _groq_vision(
    image_data_uri: str,
    transcript: str,
    language: str,
    history: list[dict] | None = None,
    rolling_summary: str | None = None,
    context: ScreenContext | None = None,
) -> VisionResult:
    """Single-step Groq vision: image + transcript (+ context) → JSON steps.

    Args:
        image_data_uri: ``data:image/jpeg;base64,...`` URI of the compressed screenshot.
        transcript: User's voice instruction.
        language: Hover AI language code.
        history: Recent conversation turns as ``[{"role": ..., "content": ...}]``.
            Text-only — screenshots from prior turns are never included.
            Injected between the system prompt and the current user turn.
        rolling_summary: Compressed summary of older turns beyond the live window.
            Injected as a second system message when present.
        context: Screen context; with elements, the screenshot is marked and
            the model is asked for ``element_id`` targets.

    Returns:
        Parsed ``VisionResult`` with resolved targets and token usage.

    Raises:
        VisionParseError: If the model returns unparseable JSON after one retry.
    """
    marked = bool(context and context.elements)
    user_content = [
        {"type": "image_url", "image_url": {"url": image_data_uri}},
        {"type": "text", "text": _build_user_text(transcript, language, context)},
    ]

    # Build message list: system → optional summary → prior turns → current turn
    messages: list[dict] = [
        {"role": "system", "content": _build_system_prompt(language, marked=marked)},
    ]
    if rolling_summary:
        messages.append({
            "role": "system",
            "content": f"Earlier in this conversation: {rolling_summary}",
        })
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_content})
    for attempt in range(2):
        if attempt == 1:
            messages.append({
                "role": "user",
                "content": "Invalid JSON. Return ONLY the raw JSON object, no markdown, no <think> tags.",
            })
        try:
            raw, usage = await _create_completion(messages, settings.vision_json_mode)
            result = _parse_vision_response(raw, context)
            result.usage = usage
            if attempt > 0:
                logger.info("Groq vision parsed on retry attempt %d", attempt + 1)
            ids = sum(1 for s in result.steps if s.target and s.target.element_id is not None)
            boxes = sum(1 for s in result.steps if s.target and s.target.element_id is None)
            logger.info(
                "Vision: %d steps (%d element targets, %d bbox targets), usage=%s",
                len(result.steps), ids, boxes, usage,
            )
            return result
        except VisionParseError:
            if attempt == 1:
                logger.error("Groq vision unparseable after retry. transcript=%r", transcript[:80])
                raise
            logger.warning("Groq vision parse failed attempt %d, retrying...", attempt + 1)
        except Exception as exc:
            logger.error("Groq vision error: %s", exc, exc_info=True)
            raise
    raise VisionParseError("Groq vision loop exited")  # pragma: no cover


async def run_vision(
    screenshot: bytes,
    transcript: str,
    language: str,
    compressed_b64: str | None = None,
    history: list[dict] | None = None,
    rolling_summary: str | None = None,
    context: ScreenContext | None = None,
) -> VisionResult:
    """Generate guided step-by-step instructions from a screenshot + voice transcript.

    Single multimodal Groq call (``settings.vision_model``) with conversation history.

    Args:
        screenshot: Raw PNG/JPEG bytes of the screen.
        transcript: Primary STT transcript.
        language: Hover AI language code (e.g. ``"en-pidgin"``).
        compressed_b64: Pre-compressed base64 JPEG from a parallel compression
            task. When provided, the internal ``_compress_screenshot`` call is
            skipped entirely, saving ~200-400ms. The caller must have used
            ``marked=bool(context and context.elements)``.
        history: Recent conversation turns as ``[{"role": ..., "content": ...}]``.
            Text-only — screenshots from prior turns are never included.
        rolling_summary: Compressed summary of older turns beyond the live window.
        context: Structured screen context from the desktop (``None`` for old
            clients). Its elements ground the step targets.

    Returns:
        A ``VisionResult`` with ordered ``BeaconStep`` instructions and a summary.

    Raises:
        VisionParseError: If the model returns unparseable JSON after one retry.
        InvalidImageError: If ``screenshot`` must be compressed and is not an image.
    """
    if compressed_b64 is None:
        marked = bool(context and context.elements)
        compressed_b64 = await asyncio.to_thread(_compress_screenshot, screenshot, marked)
    image_data_uri = f"data:image/jpeg;base64,{compressed_b64}"

    logger.info(
        "Vision: Groq pipeline (%s), context elements=%d",
        settings.vision_model, len(context.elements) if context else 0,
    )
    return await _groq_vision(
        image_data_uri, transcript, language,
        history=history, rolling_summary=rolling_summary, context=context,
    )
