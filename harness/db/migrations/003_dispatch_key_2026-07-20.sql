-- Migration 003 — duplicate protection for dispatch (task #9).
--
-- A retry must not start the same job twice: a double-tap on the send button, a
-- dropped connection that the client re-sends, the voice re-forwarding. The caller
-- stamps a dispatch_key; a second dispatch with the same key returns the existing
-- job. The partial unique index lets unkeyed jobs (NULL) coexist freely while
-- forbidding two rows from sharing a real key.

BEGIN;

ALTER TABLE jobs ADD COLUMN dispatch_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dispatch_key ON jobs(dispatch_key)
    WHERE dispatch_key IS NOT NULL;

COMMIT;
