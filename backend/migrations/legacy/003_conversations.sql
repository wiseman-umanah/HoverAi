-- Hover AI — conversation memory tables
-- Run once: psql $DATABASE_URL -f migrations/003_conversations.sql
--
-- Tables:
--   conversations         — one row per user, holds rolling_summary
--   conversation_messages — one row per turn (user + assistant)

-- ── conversations ─────────────────────────────────────────────────────────────
-- One row per user. rolling_summary is updated every CONVERSATION_WINDOW turns
-- to compress old history. Full turn-by-turn detail lives in conversation_messages.

CREATE TABLE IF NOT EXISTS conversations (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    rolling_summary TEXT
);

CREATE INDEX IF NOT EXISTS ix_conversations_user_id ON conversations (user_id);


-- ── conversation_messages ─────────────────────────────────────────────────────
-- Every turn appends two rows: one "user" (transcript) + one "assistant" (summary).
-- summarised=true rows are excluded from the LLM context window but kept for
-- the history UI and audit trail.

CREATE TABLE IF NOT EXISTS conversation_messages (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID        NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    summarised      BOOLEAN     NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS ix_conv_messages_conversation_id
    ON conversation_messages (conversation_id);
