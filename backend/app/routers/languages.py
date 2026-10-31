"""Languages router — GET /api/v1/languages.

Returns the list of languages supported by Hover AI, sourced from the
service-layer maps so there is a single source of truth.  No auth required
— this is static config the frontend uses to populate dropdowns.

Response is ordered: English first (default), then alphabetically by label.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.stt import LANGUAGE_MAP
from app.services.vision import _LANGUAGE_CONFIGS

router = APIRouter()

_DEFAULT_LANGUAGE = "en"


class LanguageOption(BaseModel):
    """A single language option for the frontend dropdown.

    Attributes:
        code: Hover AI language code (e.g. ``"en"``, ``"en-pidgin"``).
        label: Human-readable display name (e.g. ``"English"``).
        stt: Whether Sahara STT supports this language.
        tts: Whether TTS (Sahara or Groq Orpheus fallback) is available.
        default: True for the pre-selected language (English).
    """

    code: str
    label: str
    stt: bool
    tts: bool
    default: bool


@router.get("", response_model=list[LanguageOption])
async def list_languages() -> list[LanguageOption]:
    """Return all languages supported by Hover AI.

    Sourced from the service-layer maps (LANGUAGE_MAP, SAHARA_VOICE_MAP,
    _LANGUAGE_CONFIGS) so adding a new language to those maps automatically
    makes it appear here — no manual list to keep in sync.

    Languages with no entry in SAHARA_VOICE_MAP still have ``tts=true``
    because they fall back to Groq Orpheus (e.g. ``"en"``).

    Returns:
        List of ``LanguageOption`` objects, English first then alphabetical.
    """
    options: list[LanguageOption] = []

    for code in LANGUAGE_MAP:
        label_config = _LANGUAGE_CONFIGS.get(code)
        label = label_config[0] if label_config else code

        options.append(LanguageOption(
            code=code,
            label=label,
            stt=True,        # all entries in LANGUAGE_MAP have Sahara STT support
            tts=True,        # all languages have TTS — Sahara for African ones, Groq Orpheus fallback for rest
            default=(code == _DEFAULT_LANGUAGE),
        ))

    # Sort: English first, then the rest alphabetically by label
    options.sort(key=lambda o: (not o.default, o.label))
    return options
