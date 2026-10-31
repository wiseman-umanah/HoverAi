-- Hover AI — drop unused query_logs columns
-- Run against the live database after deploying the updated backend.
--
-- These five columns were added during the hackathon benchmark phase to store
-- transcripts from three concurrent STT engines. Only Groq Whisper survived
-- into the production pipeline; the others were never written to after the
-- pipeline was simplified. They are nullable so dropping them is safe and
-- requires no backfill.
--
-- Usage:
--   psql $DATABASE_URL -f migrations/002_drop_unused_query_log_columns.sql

ALTER TABLE query_logs
    DROP COLUMN IF EXISTS audio_duration_ms,
    DROP COLUMN IF EXISTS transcript_sahara,
    DROP COLUMN IF EXISTS sahara_latency_ms,
    DROP COLUMN IF EXISTS transcript_afrispeech,
    DROP COLUMN IF EXISTS afrispeech_latency_ms;
