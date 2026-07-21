-- Migration 006 — 'skipped' becomes a thing the record can say (task #10).
--
-- The panel's honesty rule is "no silent caps": a reviewer that was capped out, turned
-- off, or has no runner on this machine must be WRITTEN DOWN, because a panel that
-- quietly shrank reads exactly like a full one to anyone reading the verdicts later.
--
-- The events table had no word for it. Every allowed kind described something a model
-- DID; there was no way to record something that did not happen. So the skip note threw
-- a CHECK violation inside the reviewer thread and vanished — the exact silent shrink
-- the rule exists to prevent. (Caught by test_gauntlet_panel, not in production.)
--
-- SQLite can't alter a CHECK constraint, so this rebuilds the table. Only one index
-- depends on it and no triggers do.

BEGIN;

CREATE TABLE events_new (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    seat_id       TEXT NOT NULL REFERENCES seats(id),
    lane          TEXT NOT NULL,
    model         TEXT NOT NULL,
    family        TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN (
                      'dispatched', 'thinking', 'read', 'edit', 'write',
                      'command', 'test_run', 'browse', 'verdict', 'done', 'error',
                      'skipped'
                  )),
    target        TEXT,
    detail        TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO events_new (id, job_id, seat_id, lane, model, family, kind, target,
                        detail, created_at)
     SELECT id, job_id, seat_id, lane, model, family, kind, target, detail, created_at
       FROM events;

DROP TABLE events;
ALTER TABLE events_new RENAME TO events;

CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);

COMMIT;
