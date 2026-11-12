"""Conversation memory — rolling-summary store with DB persistence.

Architecture
────────────
One ``Conversation`` row per user in the DB.  Every query turn appends
two ``ConversationMessage`` rows (role=user + role=assistant).

Context window fed to the LLM each request:
  1. rolling_summary (system message, if one exists)
  2. Every un-summarised message, oldest first (capped at the most recent
     CONTEXT_WINDOW).  Messages are only marked summarised once they are in
     the summary, so nothing falls between summary and context (B8).

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
  CONTEXT_WINDOW  = 12  messages at most sent verbatim (normally 4–10;
                        more only if summarisation keeps failing)

Token cost per request (bounded regardless of conversation length):
  rolling_summary     ~80   tokens
  ≤10 live messages   ~400–1500 tokens (user transcripts + short answers)

Concurrency (B9)
────────────────
``save_turn`` commits the new messages first, then calls the summary LLM
with no transaction open, then *claims* the batch with
``UPDATE … SET summarised = true WHERE id IN (…) AND NOT summarised``.  If
any row was already claimed by a concurrent turn, the claim is rolled back
and the summary discarded, so each message is summarised exactly once.  The
conversation row is locked (``FOR UPDATE``, Postgres) while the summary is
appended.

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

from sqlalchemy import update
from sqlmodel import col, select

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
# Must be < SUMMARISE_AFTER.  The rest stay verbatim in the context.
SUMMARISE_BATCH: int = 8

# Upper bound on un-summarised messages sent verbatim to the LLM.  While
# summarisation works a request sees 4–10; the cap only matters while it fails.
CONTEXT_WINDOW: int = SUMMARISE_AFTER

# Maximum length of the rolling summary; older text is trimmed from the front.
SUMMARY_MAX_CHARS: int = 400


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
            select(Conversation).where(col(Conversation.user_id) == user_id)
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
                col(ConversationMessage.conversation_id) == conv.id,
                col(ConversationMessage.summarised) == False,  # noqa: E712
            )
            .order_by(col(ConversationMessage.created_at))
        )
        messages = msgs_result.scalars().all()

        live = messages[-CONTEXT_WINDOW:]
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
    """Append a turn, then summarise the oldest batch if the window is full.

    Three short transactions so no DB connection is held through the LLM
    call, and a claim step so concurrent turns never summarise the same rows
    twice (see the module docstring).

    Args:
        user_id: Owner of the conversation (logging only).
        conversation_id: The conversation's UUID string.
        user_content: The user's transcript for this turn.
        assistant_content: The assistant's answer for this turn.
    """
    conv_uuid = uuid.UUID(conversation_id)

    # 1. Append the turn and read the oldest unsummarised messages.
    async with AsyncSessionLocal() as session:
        session.add(ConversationMessage(
            conversation_id=conv_uuid, role="user", content=user_content
        ))
        session.add(ConversationMessage(
            conversation_id=conv_uuid, role="assistant", content=assistant_content
        ))
        await session.commit()
        logger.debug("Turn saved to DB — conversation_id=%s", conversation_id)

        result = await session.execute(
            select(ConversationMessage)
            .where(
                col(ConversationMessage.conversation_id) == conv_uuid,
                col(ConversationMessage.summarised) == False,  # noqa: E712
            )
            .order_by(col(ConversationMessage.created_at))
        )
        unsummarised = list(result.scalars().all())
        await session.commit()

    if len(unsummarised) < SUMMARISE_AFTER:
        return

    # 2. Summarise with no transaction open.
    to_summarise = unsummarised[:SUMMARISE_BATCH]
    new_summary = await _summarise_messages(to_summarise, user_id)
    if not new_summary:
        return

    # 3. Claim the batch and append the summary atomically.
    ids = [m.id for m in to_summarise]
    async with AsyncSessionLocal() as session:
        claimed = await session.execute(
            update(ConversationMessage)
            .where(
                col(ConversationMessage.id).in_(ids),
                col(ConversationMessage.summarised) == False,  # noqa: E712
            )
            .values(summarised=True)
            .returning(col(ConversationMessage.id))
        )
        if len(claimed.all()) != len(ids):
            await session.rollback()
            logger.info(
                "Summary batch already claimed by a concurrent turn — "
                "discarding duplicate summary for user_id=%s", user_id,
            )
            return

        conv = (
            await session.execute(
                select(Conversation).where(col(Conversation.id) == conv_uuid).with_for_update()
            )
        ).scalar_one()
        combined = _append_summary(conv.rolling_summary, new_summary)
        await session.execute(
            update(Conversation)
            .where(col(Conversation.id) == conv_uuid)
            .values(rolling_summary=combined)
        )
        await session.commit()
        logger.info(
            "Conversation summarised %d messages for user_id=%s — summary: %r",
            len(ids), user_id, combined[:80],
        )


def _append_summary(old: str | None, new: str) -> str:
    """Chain a new summary sentence onto the rolling summary.

    Args:
        old: The current rolling summary, if any.
        new: The new summary sentence.

    Returns:
        The combined summary, trimmed from the front to ``SUMMARY_MAX_CHARS``.
    """
    combined = f"{old} {new}".strip() if old else new
    return combined[-SUMMARY_MAX_CHARS:]


# ── In-memory fallback implementation ────────────────────────────────────────

def _mem_get(user_id: uuid.UUID) -> ConversationContext:
    store = _memory_store[str(user_id)]
    live = [
        {"role": m.role, "content": m.content}
        for m in store["messages"]
        if not m.summarised
    ][-CONTEXT_WINDOW:]
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
        store["summary"] = _append_summary(store["summary"], snippets)
        # Summarised turns are only needed for the summary — drop them so the
        # fallback store doesn't grow without bound.
        store["messages"] = unsummarised[SUMMARISE_BATCH:]


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
