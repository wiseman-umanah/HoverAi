-- Hover AI — add voice_gender to user_settings
-- Run once against any existing database:
--   psql $DATABASE_URL -f migrations/003_voice_gender.sql
--
-- Adds the voice_gender preference column.
-- Only "female" and "male" are valid values.
-- The actual Orpheus voice name (autumn / daniel) is resolved server-side
-- and never stored — the client only ever sees "female" or "male".

ALTER TABLE user_settings
    ADD COLUMN IF NOT EXISTS voice_gender TEXT NOT NULL DEFAULT 'female'
        CHECK (voice_gender IN ('female', 'male'));
