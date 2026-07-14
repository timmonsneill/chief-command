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
    id            TEXT PRIMARY KEY,          -- 'brain' | 'workhorse' | 'grinder_local' | 'reviewer' | 'tester' | 'mouth'
    provider      TEXT NOT NULL,             -- 'codex' | 'xai' | 'ollama' | 'claude-cli' | ...
    model         TEXT NOT NULL,
    -- Model FAMILY, not provider. Two seats can share a family via different providers
    -- (e.g. claude-cli and an Anthropic API key are both 'claude'). The gauntlet's
    -- diversity rule and the no-self-testing guard both key off this, so it must be
    -- the thing that actually determines shared blind spots.
    family        TEXT NOT NULL,             -- 'gpt' | 'claude' | 'grok' | 'qwen'
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

    -- 'done'    = the gauntlet passed. The machine is satisfied.
    -- 'shipped' = NEILL said it works on his device. Only he can move this.
    -- These are NOT the same thing, and conflating them is how agents launder
    -- confidence. From Chief's memory, learned the hard way:
    --     "Chief must NEVER say 'shipped' unless Neill has confirmed it works on
    --      his device. 'Shipped' is reserved for Neill-confirmed-working."
    -- That rule now lives here instead of in a prompt.
    status        TEXT NOT NULL DEFAULT 'todo'
                  CHECK (status IN ('todo', 'in_progress', 'review', 'done',
                                    'shipped', 'failed', 'cancelled')),
    -- Set ONLY by the owner, through mark_shipped(). No agent has a path to this.
    owner_confirmed_at TEXT,

    -- How many passing reviews this job needs before it may complete. Set at
    -- dispatch from the gauntlet config. Also from Chief's memory:
    --     "Full set, always. No 'minimum,' no 'pick two,' no 'optional depending
    --      on scope.'"
    required_reviews INTEGER NOT NULL DEFAULT 0,

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
    -- 'reviewer' reads the diff and judges. 'tester' DRIVES THE APP (Playwright) and
    -- judges what it observed. The tester guards below apply only to the latter.
    role          TEXT NOT NULL DEFAULT 'reviewer' CHECK (role IN ('reviewer', 'tester')),
    -- model family is snapshotted here so §6's "at least two families" rule is
    -- auditable, and so the no-self-family-testing guard has something to check.
    model_family  TEXT NOT NULL,
    verdict       TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'needs_human')),
    severity      TEXT CHECK (severity IN ('p0', 'p1', 'p2', 'p3')),
    summary       TEXT,
    detail        TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_verdicts_job ON verdicts(job_id);

-- ---------------------------------------------------------------------------
-- Artifacts: GROUND TRUTH. Captured by the harness, not reported by a model.
--
-- This is the anti-fabrication layer, and it exists for a specific reason:
-- GPT-5.6-sol's own system card admits it fabricates results, and METR measured a
-- cheating rate higher than any public model they'd evaluated. A tester that lies
-- doesn't just fail — it silently disables the only quality gate in the fleet,
-- because nothing checks the tester.
--
-- The defense is structural: Playwright drives the app like a real user and the
-- HARNESS writes the screenshots, traces, console logs and network dumps to disk.
-- The tester never asserts "I ran it and it worked" — it INTERPRETS artifacts it
-- did not produce and cannot forge. You can't fabricate a screenshot.
--
-- Forge (the existing Claude Code integration tester) already worked this way by
-- discipline: "If you didn't execute it and see it work, it's not tested."
-- Here that stops being a personality trait and becomes a constraint.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifacts (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN (
                      'screenshot', 'trace', 'video', 'console_log',
                      'network_log', 'dom_snapshot', 'exit_code', 'stdout', 'stderr'
                  )),
    path          TEXT,                      -- on-disk artifact
    value         TEXT,                      -- inline (e.g. an exit code)
    -- The flow this came from, e.g. "login -> dashboard -> dispatch a build"
    flow          TEXT,
    captured_by   TEXT NOT NULL DEFAULT 'harness'
                  CHECK (captured_by IN ('harness', 'playwright')),  -- never 'model'
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id);

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
-- GUARD 3 — a tester's verdict must CITE GROUND TRUTH.
--
-- Playwright is the hands; the model is the judgment. This guard governs the hands:
-- a 'tester' verdict cannot be recorded unless the harness has actually captured
-- artifacts for that job. No screenshot, no trace, no verdict.
--
-- This makes "I ran it and it worked" unsayable. The model is not trusted to report
-- what happened — only to interpret what the harness already wrote to disk.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_tester_must_cite_artifacts
BEFORE INSERT ON verdicts
WHEN NEW.role = 'tester'
BEGIN
    SELECT RAISE(ABORT, 'guard: a tester verdict requires captured artifacts — no screenshot, no verdict')
    WHERE NOT EXISTS (
        SELECT 1 FROM artifacts a WHERE a.job_id = NEW.job_id
    );
END;

-- ===========================================================================
-- GUARD 4 — a model family may not test its own work.
--
-- Playwright stops a tester FABRICATING what happened. It cannot stop a tester
-- RATIONALIZING it. If Claude builds a form and decides validation fires on blur,
-- Claude will look at a screenshot of validation firing on blur and call it correct
-- — truthfully, and wrongly. Same artifact, same blind spot, rubber stamp.
--
-- Only a different mind catches that. So: the tester's family must differ from the
-- builder's. Enforced, not requested.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_no_self_family_testing
BEFORE INSERT ON verdicts
WHEN NEW.role = 'tester'
BEGIN
    SELECT RAISE(ABORT, 'guard: a model family may not test its own build')
    WHERE NEW.model_family = (
        SELECT s.family FROM jobs j
        JOIN seats s ON s.id = j.builder_seat
        WHERE j.id = NEW.job_id
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

-- ===========================================================================
-- GUARD 5 — the full panel, always. No "pick two."
--
-- A job cannot complete without the number of passing reviews it was dispatched
-- with. Chief's memory: "Full set, always. No 'minimum,' no 'pick two,' no
-- 'optional depending on scope.'" An agent in a hurry can no longer decide that
-- this particular change didn't really need the whole gauntlet.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_full_panel
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done' AND NEW.required_reviews > 0
BEGIN
    SELECT RAISE(ABORT, 'guard: the full review panel has not reported')
    WHERE (
        SELECT COUNT(*) FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'pass'
    ) < NEW.required_reviews;
END;

-- ===========================================================================
-- GUARD 6 — a job ships when the GATES say so, not when an agent feels good.
--
-- OWNER OVERRIDE (2026-07-13): "I don't want shipped depending on me. If it is
-- reviewed and tested, then it ships."
--
-- The old rule (from Chief's memory: "Chief must NEVER say 'shipped' unless Neill
-- has confirmed it works on his device") was written when the only protection was
-- Forge's DISCIPLINE — a prompt, which a model can talk itself out of. It was a
-- human backstop for a pipeline with no teeth.
--
-- The pipeline has teeth now. So the backstop moves from Neill to the schema:
-- a job may auto-ship, but ONLY if it cleared every gate that used to require him.
--
--   • it reached 'done' (which already means: full panel, no unresolved escalation,
--     and local work carries a paid-seat signature)
--   • a TESTER drove the running app and PASSED it
--   • that tester cited real Playwright artifacts (guard 3)
--   • that tester was NOT the builder's own family (guard 4)
--
-- Neill is out of the critical path. He is NOT out of the loop — every ship is on
-- the record and reads back in the morning report.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_ship_requires_a_passing_tester
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'shipped' AND OLD.status <> 'shipped'
BEGIN
    SELECT RAISE(ABORT, 'guard: nothing ships without a passing cross-family tester on the record')
    WHERE OLD.status <> 'done'
       OR NOT EXISTS (
           SELECT 1 FROM verdicts v
           WHERE v.job_id = NEW.id
             AND v.role = 'tester'
             AND v.verdict = 'pass'
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
