"""Conversation memory — rolling-summary store with DB persistence.

Architecture
────────────
One ``Conversation`` row per user in the DB.  Every query turn appends
two ``ConversationMessage`` rows (role=user + role=assistant).

Context window fed to the LLM each request:
  1. rolling_summary (system message, if one exists)
  2. Last LIVE_WINDOW un-summarised message pairs (most recent turns verbatim)

Rolling summary trigger
───────────────────────
Fires when the number of un-summarised messages reaches SUMMARISE_AFTER.
At that point the oldest SUMMARISE_BATCH messages are:
  - summarised into one sentence via the Groq LLM
  - their ``summarised`` flag set to True in the DB
  - the new summary appended to (or replaces) ``conversations.rolling_summary``

Constants:
  SUMMARISE_AFTER = 12  messages (= 6 full turns: user+assistant each)
  SUMMARISE_BATCH =  8  messages (= 4 oldest turns to fold into summary)
  LIVE_WINDOW     =  4  messages kept verbatim (= 2 most recent turns)

Token cost per request (stable regardless of conversation length):
  rolling_summary  ~80  tokens
  4 live messages  ~600 tokens
  ─────────────────────────────
  total overhead   ~680 tokens

Persistence strategy
────────────────────
DB is always used when DATABASE_URL is reachable.  A lightweight in-memory
fallback (dict keyed by user_id UUID) is used if the DB write fails, so the
pipeline never errors out due to a history write problem.  The fallback is
also used in unit tests that don't provision a real database.
"""

import logging
import uuid
from collections import defaultdict
from typing import NamedTuple

from sqlalchemy import select, update

from app.config import settings
from app.db import AsyncSessionLocal
from app.models.models import Conversation, ConversationMessage
from app.services.llm_client import groq_client

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────

# Total un-summarised messages that trigger a summarisation pass.
# 12 = 6 full turns (each turn = 1 user + 1 assistant message).
SUMMARISE_AFTER: int = 12

# How many of the oldest messages to fold into the summary when triggered.
# Must be < SUMMARISE_AFTER.  Leaves LIVE_WINDOW messages verbatim.
SUMMARISE_BATCH: int = 8

# Messages kept verbatim in the context window (the most recent ones).
LIVE_WINDOW: int = SUMMARISE_AFTER - SUMMARISE_BATCH  # = 4


# ── In-memory fallback ────────────────────────────────────────────────────────

class _MemTurn(NamedTuple):
    role: str     # "user" | "assistant"
    content: str
    summarised: bool = False


# user_id → {"conv_id": str, "summary": str|None, "messages": list[_MemTurn]}
_memory_store: dict[str, dict] = defaultdict(
    lambda: {"conv_id": str(uuid.uuid4()), "summary": None, "messages": []}
)


# ── Public return type ────────────────────────────────────────────────────────

class ConversationContext(NamedTuple):
    """Everything the vision pipeline needs to inject history."""
    conversation_id: str
    rolling_summary: str | None        # inject as system message if not None
    recent_messages: list[dict]        # [{"role": ..., "content": ...}, ...]


# ── Core functions ────────────────────────────────────────────────────────────

async def get_or_create_conversation(user_id: uuid.UUID) -> ConversationContext:
    """Load the current conversation context for a user.

    Creates the conversation row on first call.  Returns the rolling summary
    and the most recent un-summarised messages, ready to inject into the
    LLM message list.

    Falls back to in-memory silently on any DB error.

    Args:
        user_id: Authenticated user's UUID.

    Returns:
        ``ConversationContext`` with id, rolling summary, and recent messages.
    """
    try:
        return await _db_get_or_create(user_id)
    except Exception as exc:
        logger.warning("Conversation DB read failed (%s) — using memory fallback", exc)
        return _mem_get(user_id)


async def save_turn(
    user_id: uuid.UUID,
    conversation_id: str,
    user_content: str,
    assistant_content: str,
) -> None:
    """Persist a completed turn and trigger summarisation if the window is full.

    Appends one user message + one assistant message.  If the total number of
    un-summarised messages reaches ``SUMMARISE_AFTER``, the oldest
    ``SUMMARISE_BATCH`` are summarised and their ``summarised`` flag is set.

    Runs entirely in the background — any failure is logged and swallowed so
    the main request pipeline is never affected.

    Args:
        user_id: Authenticated user's UUID.
        conversation_id: The conversation's UUID string (from get_or_create).
        user_content: The user's transcript for this turn.
        assistant_content: The assistant's summary response for this turn.
    """
    try:
        await _db_save_turn(user_id, conversation_id, user_content, assistant_content)
    except Exception as exc:
        logger.warning("Conversation DB write failed (%s) — saving to memory fallback", exc)
        _mem_save_turn(user_id, user_content, assistant_content)


# ── DB implementation ─────────────────────────────────────────────────────────

async def _db_get_or_create(user_id: uuid.UUID) -> ConversationContext:
    async with AsyncSessionLocal() as session:
        # Upsert: fetch existing or create new conversation row
        result = await session.execute(
            select(Conversation).where(Conversation.user_id == user_id)
        )
        conv = result.scalar_one_or_none()

        if conv is None:
            conv = Conversation(user_id=user_id)
            session.add(conv)
            await session.flush()  # populate conv.id
            logger.info("Conversation created for user_id=%s", user_id)

        # Fetch un-summarised messages ordered oldest→newest
        msgs_result = await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conv.id,
                ConversationMessage.summarised == False,  # noqa: E712
            )
            .order_by(ConversationMessage.created_at)
        )
        messages = msgs_result.scalars().all()

        # Keep only the LIVE_WINDOW most recent for the context
        live = messages[-LIVE_WINDOW:] if len(messages) > LIVE_WINDOW else messages
        recent = [{"role": m.role, "content": m.content} for m in live]

        await session.commit()
        return ConversationContext(
            conversation_id=str(conv.id),
            rolling_summary=conv.rolling_summary,
            recent_messages=recent,
        )


async def _db_save_turn(
    user_id: uuid.UUID,
    conversation_id: str,
    user_content: str,
    assistant_content: str,
) -> None:
    async with AsyncSessionLocal() as session:
        conv_uuid = uuid.UUID(conversation_id)

        # Append the two new messages
        session.add(ConversationMessage(
            conversation_id=conv_uuid, role="user", content=user_content
        ))
        session.add(ConversationMessage(
            conversation_id=conv_uuid, role="assistant", content=assistant_content
        ))

        # Count current un-summarised messages (including the two just added)
        count_result = await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conv_uuid,
                ConversationMessage.summarised == False,  # noqa: E712
            )
            .order_by(ConversationMessage.created_at)
        )
        all_unsummarised = count_result.scalars().all()

        if len(all_unsummarised) >= SUMMARISE_AFTER:
            # Summarise the oldest SUMMARISE_BATCH messages
            to_summarise = all_unsummarised[:SUMMARISE_BATCH]
            new_summary = await _summarise_messages(to_summarise, user_id)

            if new_summary:
                # Fetch current rolling_summary to chain it
                conv_result = await session.execute(
                    select(Conversation).where(Conversation.id == conv_uuid)
                )
                conv = conv_result.scalar_one()
                old_summary = conv.rolling_summary

                combined = (
                    f"{old_summary} {new_summary}".strip()
                    if old_summary
                    else new_summary
                )
                # Trim to 400 chars so it never grows unbounded
                if len(combined) > 400:
                    combined = combined[-400:]

                await session.execute(
                    update(Conversation)
                    .where(Conversation.id == conv_uuid)
                    .values(rolling_summary=combined)
                )

                # Mark summarised rows
                ids_to_mark = [m.id for m in to_summarise]
                await session.execute(
                    update(ConversationMessage)
                    .where(ConversationMessage.id.in_(ids_to_mark))
                    .values(summarised=True)
                )
                logger.info(
                    "Conversation summarised %d messages for user_id=%s — summary: %r",
                    len(to_summarise), user_id, combined[:80],
                )

        await session.commit()
        logger.debug("Turn saved to DB — conversation_id=%s", conversation_id)


# ── In-memory fallback implementation ────────────────────────────────────────

def _mem_get(user_id: uuid.UUID) -> ConversationContext:
    store = _memory_store[str(user_id)]
    live = [
        {"role": m.role, "content": m.content}
        for m in store["messages"]
        if not m.summarised
    ][-LIVE_WINDOW:]
    return ConversationContext(
        conversation_id=store["conv_id"],
        rolling_summary=store["summary"],
        recent_messages=live,
    )


def _mem_save_turn(user_id: uuid.UUID, user_content: str, assistant_content: str) -> None:
    store = _memory_store[str(user_id)]
    store["messages"].append(_MemTurn(role="user", content=user_content))
    store["messages"].append(_MemTurn(role="assistant", content=assistant_content))

    unsummarised = [m for m in store["messages"] if not m.summarised]
    if len(unsummarised) >= SUMMARISE_AFTER:
        to_summarise = unsummarised[:SUMMARISE_BATCH]
        # Build a simple text summary inline (no LLM call in the sync fallback)
        snippets = "; ".join(
            m.content[:60] for m in to_summarise if m.role == "user"
        )
        store["summary"] = (
            f"{store['summary']} {snippets}".strip()
            if store["summary"]
            else snippets
        )
        if len(store["summary"]) > 400:
            store["summary"] = store["summary"][-400:]
        # Mark as summarised
        store["messages"] = [
            _MemTurn(m.role, m.content, True) if m in to_summarise else m
            for m in store["messages"]
        ]


# ── LLM summarisation helper ──────────────────────────────────────────────────

async def _summarise_messages(
    messages: list[ConversationMessage],
    user_id: uuid.UUID,
) -> str:
    """Ask the LLM to compress a batch of messages into one short summary.

    Args:
        messages: The ``ConversationMessage`` rows to summarise.
        user_id: Used only for logging.

    Returns:
        A single sentence summary string, or empty string on failure.
    """
    dialogue = "\n".join(
        f"{m.role.upper()}: {m.content}" for m in messages
    )
    prompt = (
        "Summarise the following conversation excerpt in ONE sentence. "
        "Focus on what the user was trying to do and what was accomplished. "
        "Output ONLY the summary sentence — no preamble, no quotes.\n\n"
        f"{dialogue}"
    )
    try:
        resp = await groq_client.chat.completions.create(
            model=settings.vision_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=80,
            temperature=0.1,
        )
        summary = (resp.choices[0].message.content or "").strip()
        return summary
    except Exception as exc:
        logger.warning("Summary LLM call failed (%s) — skipping summarisation", exc)
        return ""
