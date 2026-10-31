-- Hover AI — add refresh_tokens table
-- Run once against any existing database:
--   psql $DATABASE_URL -f migrations/002_refresh_tokens.sql

-- ── refresh_tokens ────────────────────────────────────────────────────────────
-- Stores long-lived opaque tokens (90-day default) used to obtain new
-- short-lived access tokens (15-min JWT) without re-authentication.
--
-- Strategy:
--   - token is a 128-char random hex string generated server-side.
--   - Each sign-in/sign-up creates one row.
--   - On refresh, the old row is marked revoked=true and a new row is inserted
--     (token rotation — use-once semantics).
--   - On sign-out, the row is revoked immediately.
--   - Expired + revoked rows can be cleaned up periodically with:
--       DELETE FROM refresh_tokens
--        WHERE revoked = true OR expires_at < now() - interval '1 day';

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token       TEXT        NOT NULL UNIQUE,
    device_id   TEXT,                          -- optional: "electron-linux", "electron-mac"
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked     BOOLEAN     NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_refresh_tokens_token    ON refresh_tokens(token);
CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id  ON refresh_tokens(user_id);
