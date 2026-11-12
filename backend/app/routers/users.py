"""Users routes — GET/PATCH /api/v1/users/me, GET/PATCH /api/v1/users/me/settings."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.auth import get_current_user
from app.db import get_session
from app.models.models import User, UserSettings
from app.schemas.users import (
    LanguageCode,
    UserResponse,
    UserSettingsResponse,
    UserSettingsUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class UserProfileUpdate(BaseModel):
    """Partial update body for PATCH /users/me.

    Attributes:
        language: Hover AI language code (e.g. ``"en-pidgin"``, ``"yo"``).
            Controls STT language hint, vision response language, and TTS voice.
    """

    language: Optional[LanguageCode] = None


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Return the authenticated user's public profile.

    Args:
        current_user: ``User`` instance injected by ``get_current_user``.

    Returns:
        A ``UserResponse`` with the user's profile fields.
    """
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        language=current_user.language,
    )


@router.patch("/me", response_model=UserResponse)
async def patch_me(
    body: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserResponse:
    """Update the authenticated user's profile fields.

    Currently supports updating ``language`` only.  Other profile fields
    (``full_name``, ``email``) are not patchable through this endpoint.

    Args:
        body: Partial profile update — only non-None fields are applied.
        current_user: ``User`` instance injected by ``get_current_user``.
        session: Async database session injected by ``get_session``.

    Returns:
        The updated ``UserResponse``.

    Raises:
        HTTPException 400: If no fields are provided.
    """
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided to update.",
        )

    result = await session.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    for field, value in updates.items():
        setattr(user, field, value)

    session.add(user)
    await session.flush()
    await session.refresh(user)
    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        language=user.language,
    )


@router.get("/me/settings", response_model=UserSettingsResponse)
async def get_settings(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserSettingsResponse:
    """Return the authenticated user's app settings.

    Args:
        current_user: ``User`` instance injected by ``get_current_user``.
        session: Async database session injected by ``get_session``.

    Returns:
        A ``UserSettingsResponse`` with the current settings values.

    Raises:
        HTTPException 404: If the settings row does not exist (should not
            happen — signup always creates a default row).
    """
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settings not found for this user.",
        )
    return UserSettingsResponse.model_validate(row)


@router.patch("/me/settings", response_model=UserSettingsResponse)
async def update_settings(
    body: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserSettingsResponse:
    """Partially update the authenticated user's app settings.

    Only fields present in the request body are updated (PATCH semantics).
    Fields omitted retain their current values.

    Args:
        body: Partial settings update — only non-None fields are applied.
        current_user: ``User`` instance injected by ``get_current_user``.
        session: Async database session injected by ``get_session``.

    Returns:
        A ``UserSettingsResponse`` reflecting the updated settings.

    Raises:
        HTTPException 400: If no fields are provided in the request body.
        HTTPException 404: If the settings row does not exist.
    """
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided to update.",
        )

    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settings not found for this user.",
        )

    for field, value in updates.items():
        setattr(row, field, value)

    session.add(row)
    await session.flush()
    await session.refresh(row)
    return UserSettingsResponse.model_validate(row)
