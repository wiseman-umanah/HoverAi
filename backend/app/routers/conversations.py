"""Conversations router — GET /api/v1/conversations/history.

Read-only endpoint. Returns the authenticated user's conversation messages
grouped by calendar day (UTC), ordered newest day first.

Each day's messages are ordered oldest → newest within the day so the
frontend can render them as a readable chat log.

Summarised messages (already folded into the rolling summary) are excluded
— the UI shows only the verbatim turns the user actually said and heard.
"""

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from app.auth import get_current_user
from app.db import get_session
from app.models.models import Conversation, ConversationMessage, User

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Response schemas ──────────────────────────────────────────────────────────

class ConversationMessageOut(BaseModel):
    """A single turn in the conversation log.

    Attributes:
        id: Message UUID.
        role: ``"user"`` or ``"assistant"``.
        content: Transcript (user) or summary response (assistant).
        created_at: UTC timestamp of the message.
    """

    id: str
    role: str
    content: str
    created_at: datetime


class ConversationDayOut(BaseModel):
    """All messages from a single calendar day (UTC).

    Attributes:
        date: ISO date string e.g. ``"2025-01-15"``.
        messages: Ordered oldest → newest within the day.
    """

    date: str
    messages: list[ConversationMessageOut]


class ConversationHistoryOut(BaseModel):
    """Full history response.

    Attributes:
        conversation_id: The user's conversation UUID.
        days: Days ordered newest first. Each day contains its messages
              ordered oldest first.
    """

    conversation_id: str
    days: list[ConversationDayOut]


# ── Route ─────────────────────────────────────────────────────────────────────

@router.get("/history", response_model=ConversationHistoryOut)
async def get_history(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationHistoryOut:
    """Return the user's full conversation history grouped by day.

    Only un-summarised (verbatim) messages are returned — summarised rows
    have already been compressed into the rolling summary and would be
    redundant to display.

    Days are ordered newest first. Messages within each day are ordered
    oldest first so they read naturally top-to-bottom.

    Returns an empty ``days`` list if the user has no conversation yet.
    """
    # Look up the user's conversation
    conv_result = await session.execute(
        select(Conversation).where(Conversation.user_id == current_user.id)
    )
    conv = conv_result.scalar_one_or_none()

    if conv is None:
        # No conversation started yet — return empty history
        return ConversationHistoryOut(
            conversation_id=str(uuid.uuid4()),
            days=[],
        )

    # Fetch all un-summarised messages, oldest first
    msgs_result = await session.execute(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conv.id,
            ConversationMessage.summarised == False,  # noqa: E712
        )
        .order_by(col(ConversationMessage.created_at))
    )
    messages = msgs_result.scalars().all()

    # Group by calendar day (UTC date string "YYYY-MM-DD")
    by_day: dict[str, list[ConversationMessageOut]] = defaultdict(list)
    for msg in messages:
        # Ensure timezone-aware before converting
        ts = msg.created_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        day_key = ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
        by_day[day_key].append(
            ConversationMessageOut(
                id=str(msg.id),
                role=msg.role,
                content=msg.content,
                created_at=ts,
            )
        )

    # Sort days newest first
    days = [
        ConversationDayOut(date=date, messages=msgs)
        for date, msgs in sorted(by_day.items(), reverse=True)
    ]

    logger.debug(
        "History fetched for user_id=%s — %d days, %d messages",
        current_user.id, len(days), len(messages),
    )

    return ConversationHistoryOut(
        conversation_id=str(conv.id),
        days=days,
    )
