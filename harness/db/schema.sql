-- Chief Command v2 — harness job store
--
-- This is the layer OpenClaw does NOT give us. Its subagent sessions auto-archive
-- after ~60 minutes and get soft-deleted; that is session archival, not durable job
-- history. Spec §7 wants "what did the overnight run do?" to be answerable. This is that.
--
-- Two rules are enforced STRUCTURALLY here rather than in a prompt, because rules that
-- live in prompts quietly stop holding at 4am:
--   1. Local-model output can never reach `done` without a higher-tier passing review
--      (spec §4.3 / §9 — "enforce in the pipeline itself, not by convention").
--   2. Per-seat spend caps (OpenClaw core has no per-agent budget of any kind).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Seats: the named roles from spec §4. Providers are swappable behind these.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS seats (
    id            TEXT PRIMARY KEY,          -- 'orchestrator' | 'workhorse' | 'grinder' | 'reviewer' | 'head'
    provider      TEXT NOT NULL,             -- 'codex' | 'xai' | 'ollama' | 'claude-cli' | ...
    model         TEXT NOT NULL,
    -- tier drives the review guard below. 'local' output is never trusted on its own.
    tier          TEXT NOT NULL CHECK (tier IN ('local', 'subscription', 'metered')),
    -- NULL = uncapped. Cents/day, checked against the usage table.
    daily_cap_cents INTEGER,
    enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    notes         TEXT
);

-- ---------------------------------------------------------------------------
-- Jobs: one row per dispatched unit of work. This is the audit trail.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    started_at    TEXT,
    finished_at   TEXT,

    request       TEXT NOT NULL,             -- what was asked, verbatim (§7)
    origin        TEXT NOT NULL DEFAULT 'text'
                  CHECK (origin IN ('text', 'voice', 'cron', 'relay')),  -- 'relay' = Jess (§10)

    builder_seat  TEXT NOT NULL REFERENCES seats(id),
    run_id        TEXT,                      -- OpenClaw sessions_spawn run id
    session_key   TEXT,                      -- agent:<id>:subagent:<uuid>

    -- Git as audit trail (§7). Worktree-per-job, main untouched until merged.
    branch        TEXT,
    worktree      TEXT,

    status        TEXT NOT NULL DEFAULT 'todo'
                  CHECK (status IN ('todo', 'in_progress', 'review', 'done', 'failed', 'cancelled')),
    result        TEXT,                      -- final summary / diff ref
    error         TEXT,

    -- Voice wants a spoken-length answer; the text log keeps the detail (§5.3).
    spoken_summary TEXT,

    attempts      INTEGER NOT NULL DEFAULT 0,
    parent_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_run     ON jobs(run_id);

-- ---------------------------------------------------------------------------
-- Verdicts: the gauntlet's output (§6). One row per reviewer per job.
-- reviewer_tier is denormalized at write time so a later seat re-tiering cannot
-- retroactively validate a job that was never properly reviewed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS verdicts (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    reviewer_seat TEXT NOT NULL REFERENCES seats(id),
    reviewer_tier TEXT NOT NULL CHECK (reviewer_tier IN ('local', 'subscription', 'metered')),
    -- model family is recorded so the §6 "at least two families" rule is auditable
    model_family  TEXT NOT NULL,
    verdict       TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'needs_human')),
    severity      TEXT CHECK (severity IN ('p0', 'p1', 'p2', 'p3')),
    summary       TEXT,
    detail        TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_verdicts_job ON verdicts(job_id);

-- ---------------------------------------------------------------------------
-- Usage: per-seat spend, so the caps in `seats` mean something.
-- OpenClaw core has NO per-agent cost or token budget. This is ours.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usage (
    id            INTEGER PRIMARY KEY,
    seat_id       TEXT NOT NULL REFERENCES seats(id),
    job_id        INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    day           TEXT NOT NULL DEFAULT (date('now')),
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cents    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_seat_day ON usage(seat_id, day);

-- ===========================================================================
-- GUARD 1 — local output never ships unreviewed (§4.3, §9)
--
-- A job built by a 'local' tier seat cannot reach 'done' without at least one
-- PASSING verdict from a higher tier. This is a transition guard, not a
-- convention: the write simply fails.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_local_output_needs_review
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done'
BEGIN
    SELECT RAISE(ABORT, 'guard: local-built job requires a passing subscription-tier review before done')
    WHERE (SELECT tier FROM seats WHERE id = NEW.builder_seat) = 'local'
      AND NOT EXISTS (
          SELECT 1 FROM verdicts v
          WHERE v.job_id = NEW.id
            AND v.reviewer_tier IN ('subscription', 'metered')
            AND v.verdict = 'pass'
      );
END;

-- ===========================================================================
-- GUARD 2 — an unresolved 'needs_human' verdict blocks completion.
-- Escalations must be answered, not outrun (§6).
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_unresolved_escalation
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done'
BEGIN
    SELECT RAISE(ABORT, 'guard: job has an unresolved needs_human verdict')
    WHERE EXISTS (
        SELECT 1 FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'needs_human'
    );
END;

-- Keep updated_at honest.
CREATE TRIGGER IF NOT EXISTS jobs_touch_updated_at
AFTER UPDATE ON jobs
FOR EACH ROW
BEGIN
    UPDATE jobs SET updated_at = datetime('now') WHERE id = NEW.id;
END;

-- ---------------------------------------------------------------------------
-- The §7 voice query: "what did the overnight run do?"
-- ---------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS job_report AS
SELECT
    j.id,
    j.created_at,
    j.finished_at,
    j.request,
    j.origin,
    j.builder_seat,
    s.provider   AS builder_provider,
    s.tier       AS builder_tier,
    j.status,
    j.branch,
    j.spoken_summary,
    (SELECT COUNT(*) FROM verdicts v WHERE v.job_id = j.id)                        AS review_count,
    (SELECT COUNT(DISTINCT v.model_family) FROM verdicts v WHERE v.job_id = j.id)  AS families_reviewed,
    (SELECT COUNT(*) FROM verdicts v WHERE v.job_id = j.id AND v.verdict = 'fail') AS fail_count,
    (SELECT COUNT(*) FROM verdicts v WHERE v.job_id = j.id AND v.verdict = 'needs_human') AS escalations
FROM jobs j
JOIN seats s ON s.id = j.builder_seat;
