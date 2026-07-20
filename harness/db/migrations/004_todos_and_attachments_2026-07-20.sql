-- Migration 004 — todos + attachments for the command center (tasks #15, #16).
--
-- Todos that belong to a PROJECT (so they stop jumping window to window), grouped
-- into free-form sections the owner names himself ("Now", "Later", "Post-launch").
-- Attachments so images and files can be pinned to a project or a job.

BEGIN;

CREATE TABLE IF NOT EXISTS todos (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    section       TEXT,
    text          TEXT NOT NULL,
    done          INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    owner_only    INTEGER NOT NULL DEFAULT 0 CHECK (owner_only IN (0, 1)),
    position      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    done_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_todos_project ON todos(project_id, section, position, id);

CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT REFERENCES projects(id) ON DELETE SET NULL,
    job_id        INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    filename      TEXT NOT NULL,
    stored_path   TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('image', 'file')),
    size_bytes    INTEGER,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_attachments_project ON attachments(project_id);
CREATE INDEX IF NOT EXISTS idx_attachments_job ON attachments(job_id);

COMMIT;
