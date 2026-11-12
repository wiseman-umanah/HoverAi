"""User and settings schemas."""

from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.services.stt import LANGUAGE_MAP


def _known_language(value: str) -> str:
    """Reject language codes the STT/vision/TTS maps don't know (B13).

    Args:
        value: The submitted Hover language code.

    Returns:
        The unchanged code.

    Raises:
        ValueError: If the code is not in ``LANGUAGE_MAP`` (→ 422).
    """
    if value not in LANGUAGE_MAP:
        raise ValueError(f"Unsupported language: {value!r}.")
    return value


# A Hover language code accepted by every provider map (see routers/languages.py).
LanguageCode = Annotated[str, AfterValidator(_known_language)]


class UserResponse(BaseModel):
    """Public profile returned from GET /users/me.

    Attributes:
        id: User UUID.
        email: Registered email address.
        full_name: Display name.
        language: Preferred language code.
    """

    id: str
    email: EmailStr
    full_name: Optional[str]
    language: str


# Maps the public-facing gender token to the actual Orpheus voice name.
# The voice names are never exposed to the client — only "female" / "male".
VOICE_GENDER_MAP: dict[str, str] = {
    "female": "autumn",
    "male": "daniel",
}


class UserSettingsResponse(BaseModel):
    """User settings returned from GET /api/v1/users/me/settings.

    Fields mirror ``AppSettings`` in ``hover-app/src/preload/index.d.ts``
    exactly. Keep both in sync when adding new settings.

    Attributes:
        overlay_opacity: Overlay window opacity (0.4 – 1.0).
        overlay_size: Widget size variant.
        mic_sensitivity: Microphone sensitivity level (1 – 10).
        sound_effects: Whether UI sound effects are enabled.
        notifications: Whether desktop notifications are enabled.
        wake_word_enabled: Whether wake-word detection is active.
        launch_at_login: Whether the app launches at system login.
        show_in_taskbar: Whether the app appears in the system taskbar.
        voice_gender: Preferred AI voice gender — ``"female"`` or ``"male"``.
    """

    model_config = ConfigDict(from_attributes=True)

    overlay_opacity: float
    overlay_size: Literal["compact", "default", "large"]
    mic_sensitivity: int
    sound_effects: bool
    notifications: bool
    wake_word_enabled: bool
    launch_at_login: bool
    show_in_taskbar: bool
    voice_gender: Literal["female", "male"]


class UserSettingsUpdate(BaseModel):
    """Partial settings update body for PATCH /users/me/settings.

    All fields are Optional — only provided fields are updated (PATCH semantics).

    Attributes:
        overlay_opacity: Overlay window opacity (0.4 – 1.0).
        overlay_size: Widget size variant.
        mic_sensitivity: Microphone sensitivity level (1 – 10).
        sound_effects: Whether UI sound effects are enabled.
        notifications: Whether desktop notifications are enabled.
        wake_word_enabled: Whether wake-word detection is active.
        launch_at_login: Whether the app launches at system login.
        show_in_taskbar: Whether the app appears in the system taskbar.
        voice_gender: Preferred AI voice gender — ``"female"`` or ``"male"``.
    """

    overlay_opacity: Optional[float] = Field(default=None, ge=0.4, le=1.0)
    overlay_size: Optional[Literal["compact", "default", "large"]] = None
    mic_sensitivity: Optional[int] = Field(default=None, ge=1, le=10)
    sound_effects: Optional[bool] = None
    notifications: Optional[bool] = None
    wake_word_enabled: Optional[bool] = None
    launch_at_login: Optional[bool] = None
    show_in_taskbar: Optional[bool] = None
    voice_gender: Optional[Literal["female", "male"]] = None
