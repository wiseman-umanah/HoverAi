"""Conversation memory tests (B8 context window, B9 summary claiming)."""

import uuid

import pytest
from sqlmodel import col, select, update

from app.db import AsyncSessionLocal
from app.models.models import Conversation, ConversationMessage, User
from app.services import conversation as conv_mod
from app.services.conversation import (
    SUMMARISE_AFTER,
    SUMMARISE_BATCH,
    get_or_create_conversation,
    save_turn,
)


async def _make_user() -> uuid.UUID:
    """Insert a bare user row and return its id.

    Returns:
        The new user's UUID.
    """
    async with AsyncSessionLocal() as session:
        user = User(email=f"{uuid.uuid4().hex}@example.com", hashed_password="x")
        session.add(user)
        await session.commit()
        return user.id


async def _save_turns(user_id: uuid.UUID, conv_id: str, n: int, start: int = 0) -> None:
    """Save ``n`` numbered turns.

    Args:
        user_id: Conversation owner.
        conv_id: Conversation UUID string.
        n: Number of turns (2 messages each).
        start: First turn number.
    """
    for i in range(start, start + n):
        await save_turn(user_id, conv_id, f"q{i}", f"a{i}")


async def _summary(conv_id: str) -> str | None:
    """Read the rolling summary straight from the DB.

    Args:
        conv_id: Conversation UUID string.

    Returns:
        The stored rolling summary.
    """
    async with AsyncSessionLocal() as session:
        conv = (
            await session.execute(
                select(Conversation).where(col(Conversation.id) == uuid.UUID(conv_id))
            )
        ).scalar_one()
        return conv.rolling_summary


@pytest.fixture()
def fake_summary(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace the LLM summariser; records the batches it was given.

    Returns:
        A list that receives each summarised batch's message contents.
    """
    calls: list[list[str]] = []

    async def _fake(messages, user_id):  # noqa: ANN001, ARG001
        calls.append([m.content for m in messages])
        return f"summary{len(calls)}"

    monkeypatch.setattr(conv_mod, "_summarise_messages", _fake)
    return calls


async def test_context_contains_every_unsummarised_message(fake_summary) -> None:
    """B8: after summarising, nothing falls between summary and context."""
    user_id = await _make_user()
    ctx = await get_or_create_conversation(user_id)

    await _save_turns(user_id, ctx.conversation_id, SUMMARISE_AFTER // 2 - 1)
    ctx = await get_or_create_conversation(user_id)
    assert len(ctx.recent_messages) == SUMMARISE_AFTER - 2
    assert ctx.rolling_summary is None
    assert fake_summary == []

    # One more turn reaches SUMMARISE_AFTER → the oldest batch is folded.
    await _save_turns(user_id, ctx.conversation_id, 1, start=SUMMARISE_AFTER // 2 - 1)
    ctx = await get_or_create_conversation(user_id)
    assert ctx.rolling_summary == "summary1"
    assert fake_summary[0] == [f"{r}{i}" for i in range(SUMMARISE_BATCH // 2) for r in "qa"]
    contents = [m["content"] for m in ctx.recent_messages]
    first_live = SUMMARISE_BATCH // 2
    assert contents == [
        f"{r}{i}" for i in range(first_live, SUMMARISE_AFTER // 2) for r in "qa"
    ]


async def test_summary_discarded_when_batch_already_claimed(monkeypatch) -> None:
    """B9: a concurrent turn that claimed the batch first wins; no double summary."""
    user_id = await _make_user()
    ctx = await get_or_create_conversation(user_id)

    async def _racing(messages, uid):  # noqa: ANN001, ARG001
        # Simulate another turn claiming the same rows while the LLM runs.
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ConversationMessage)
                .where(col(ConversationMessage.id).in_([m.id for m in messages]))
                .values(summarised=True)
            )
            await session.execute(
                update(Conversation)
                .where(col(Conversation.id) == uuid.UUID(ctx.conversation_id))
                .values(rolling_summary="winner")
            )
            await session.commit()
        return "loser"

    monkeypatch.setattr(conv_mod, "_summarise_messages", _racing)
    await _save_turns(user_id, ctx.conversation_id, SUMMARISE_AFTER // 2)
    assert await _summary(ctx.conversation_id) == "winner"


async def test_failed_summary_keeps_messages_unsummarised(monkeypatch) -> None:
    """An empty summary (LLM failure) leaves every message in the context."""
    user_id = await _make_user()
    ctx = await get_or_create_conversation(user_id)

    async def _fail(messages, uid):  # noqa: ANN001, ARG001
        return ""

    monkeypatch.setattr(conv_mod, "_summarise_messages", _fail)
    await _save_turns(user_id, ctx.conversation_id, SUMMARISE_AFTER // 2)
    ctx = await get_or_create_conversation(user_id)
    assert ctx.rolling_summary is None
    assert len(ctx.recent_messages) == SUMMARISE_AFTER


def test_memory_fallback_bounds_store() -> None:
    """The in-memory fallback summarises and drops folded turns."""
    user_id = uuid.uuid4()
    for i in range(SUMMARISE_AFTER // 2):
        conv_mod._mem_save_turn(user_id, f"q{i}", f"a{i}")
    store = conv_mod._memory_store[str(user_id)]
    assert len(store["messages"]) == SUMMARISE_AFTER - SUMMARISE_BATCH
    ctx = conv_mod._mem_get(user_id)
    assert ctx.rolling_summary == "q0; q1; q2; q3"
    assert len(ctx.recent_messages) == SUMMARISE_AFTER - SUMMARISE_BATCH
