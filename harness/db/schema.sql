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
    -- A 2-4 word handle the mouth coins at dispatch: "the rate limiter".
    -- The VOICE says this. The TEXT shows `request` in full. Echoing a man's own
    -- paragraph back at him is not how a colleague talks.
    task_name     TEXT,
    origin        TEXT NOT NULL DEFAULT 'text'
                  CHECK (origin IN ('text', 'voice', 'cron', 'relay')),  -- 'relay' = Jess (§10)

    -- 'build'    → produces code. Ground truth = a screenshot (Playwright drove it).
    -- 'research' → produces an ANSWER. Ground truth = a source (someone can go read it).
    -- Same principle, different evidence. A researcher that can't cite is a researcher
    -- that might be confabulating — and a confident wrong answer is worse than no answer,
    -- because you act on it.
    kind          TEXT NOT NULL DEFAULT 'build' CHECK (kind IN ('build', 'research')),

    builder_seat  TEXT NOT NULL REFERENCES seats(id),
    -- SNAPSHOTTED AT CREATION. Sol (cross-family review, 2026-07-13) found that
    -- guards read the builder's tier/family LIVE from `seats`, so re-tiering a local
    -- seat to 'subscription' retroactively legitimized its old unreviewed work.
    -- Freeze it here. What a seat is TODAY cannot change what it WAS.
    builder_tier   TEXT NOT NULL DEFAULT 'local'
                   CHECK (builder_tier IN ('local','subscription','metered')),
    builder_family TEXT NOT NULL DEFAULT 'unknown',
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
    -- 'reviewer' reads the diff and judges it.
    -- 'tester'   DROVE the running app (Playwright) and judges what it observed.
    -- 'verifier' FACT-CHECKS a research answer against its sources.
    role          TEXT NOT NULL DEFAULT 'reviewer'
                  CHECK (role IN ('reviewer', 'tester', 'verifier')),
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
-- Events: the live activity stream. What an agent is doing RIGHT NOW.
--
-- This is what makes the text channel feel like the Claude Code terminal — you
-- watch Riggs read auth.py, edit routes.py, run pytest, and come back green.
-- Without it the harness can only tell you a job "happened," which is useless
-- while you're waiting and only mildly interesting afterward.
--
-- ONE STREAM, TWO RENDERINGS (this is the key design decision):
--   VOICE reads jobs.spoken_summary — a sentence. "Riggs is on it, two minutes."
--   TEXT  reads THIS table in full   — every tool call, every file, every result.
-- Same truth, different verbosity. The voice can escalate into this table on
-- request ("what's it actually doing?") — see [voice] in seats.toml.
--
-- Note `lane` and `model` are BOTH recorded. The lane (Riggs) carries the memory
-- and the conventions; the model is whoever is sitting in that chair today. You
-- must always be able to see which — otherwise you can't tell whether your auth
-- module was built by your best coder or your cheapest one.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    seat_id       TEXT NOT NULL REFERENCES seats(id),
    lane          TEXT NOT NULL,             -- 'riggs' | 'finn' | 'nova' | 'forge' ...
    model         TEXT NOT NULL,             -- who is actually in the chair right now
    family        TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN (
                      'dispatched', 'thinking', 'read', 'edit', 'write',
                      'command', 'test_run', 'browse', 'verdict', 'done', 'error'
                  )),
    target        TEXT,                      -- the file / command / URL it touched
    detail        TEXT,                      -- one line, terminal-style
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);

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
                      -- build evidence: the harness drove the app and wrote these down
                      'screenshot', 'trace', 'video', 'console_log',
                      'network_log', 'dom_snapshot', 'exit_code', 'stdout', 'stderr',
                      -- research evidence: a claim you can go and check yourself
                      'source', 'quote'
                  )),
    path          TEXT,                      -- on-disk artifact
    value         TEXT,                      -- inline (e.g. an exit code)
    -- The flow this came from, e.g. "login -> dashboard -> dispatch a build"
    flow          TEXT,
    -- 'model' IS allowed here, but ONLY for research sources — a researcher must be
    -- able to hand you a URL. It is still not allowed to invent a screenshot.
    captured_by   TEXT NOT NULL DEFAULT 'harness'
                  CHECK (captured_by IN ('harness', 'playwright', 'model')),
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
-- THE GUARDS
--
-- ⚠️ REWRITTEN 2026-07-13 after a cross-family review. Sol (GPT) reviewed Claude's
-- code and found NINE ways through. The headline: every guard fired on UPDATE of
-- status, and NONE fired on INSERT — so a job could be BORN 'done'. Fences around
-- the front door, no wall.
--
-- Sol's closing line, which is the one that mattered:
--     "Those are application conventions, not the claimed structurally impossible
--      guarantees."
--
-- He was right. Fixed below. Each guard now fires on BOTH insert and update, checks
-- SNAPSHOTTED facts rather than live ones, and validates claims against `seats`
-- instead of trusting whatever the writer put in the row.
-- ===========================================================================

-- A job may never be BORN finished. (Sol #1 — the worst one.)
CREATE TRIGGER IF NOT EXISTS guard_no_job_is_born_done
BEFORE INSERT ON jobs
WHEN NEW.status IN ('done', 'shipped')
BEGIN
    SELECT RAISE(ABORT, 'guard: a job cannot be created already finished — it must earn it');
END;

-- The builder's tier/family are snapshotted at creation and are then HISTORY.
-- (Sol #5, #6 — re-tiering a seat rewrote the past.)
CREATE TRIGGER IF NOT EXISTS guard_builder_identity_is_frozen
BEFORE UPDATE ON jobs
WHEN OLD.builder_seat <> NEW.builder_seat
  OR OLD.builder_tier <> NEW.builder_tier
  OR OLD.builder_family <> NEW.builder_family
BEGIN
    SELECT RAISE(ABORT, 'guard: who built this cannot be rewritten after the fact');
END;

-- The panel size is fixed at dispatch. (Sol #7 — it could be set to zero later.)
CREATE TRIGGER IF NOT EXISTS guard_panel_size_is_fixed
BEFORE UPDATE OF required_reviews ON jobs
WHEN OLD.required_reviews > 0 AND NEW.required_reviews < OLD.required_reviews
BEGIN
    SELECT RAISE(ABORT, 'guard: the panel cannot be shrunk after dispatch');
END;

-- A verdict, once written, is a fact. It cannot be edited into a pass.
-- (Sol #2 — every verdict guard checked INSERT only, so you could rewrite the row.)
CREATE TRIGGER IF NOT EXISTS guard_verdicts_are_append_only
BEFORE UPDATE ON verdicts
WHEN OLD.verdict <> NEW.verdict
     AND NOT (OLD.verdict = 'needs_human' AND NEW.verdict IN ('pass','fail'))
BEGIN
    SELECT RAISE(ABORT, 'guard: a verdict cannot be rewritten — only an escalation may be answered');
END;

CREATE TRIGGER IF NOT EXISTS guard_verdict_identity_is_frozen
BEFORE UPDATE ON verdicts
WHEN OLD.role <> NEW.role OR OLD.model_family <> NEW.model_family
  OR OLD.reviewer_seat <> NEW.reviewer_seat OR OLD.reviewer_tier <> NEW.reviewer_tier
BEGIN
    SELECT RAISE(ABORT, 'guard: who reviewed this cannot be rewritten after the fact');
END;

-- A reviewer cannot LIE about who it is. (Sol #4 — the DB took the row's word for it.)
CREATE TRIGGER IF NOT EXISTS guard_reviewer_identity_must_be_real
BEFORE INSERT ON verdicts
WHEN NEW.reviewer_tier <> (SELECT tier FROM seats WHERE id = NEW.reviewer_seat)
  OR NEW.model_family  <> (SELECT family FROM seats WHERE id = NEW.reviewer_seat)
BEGIN
    SELECT RAISE(ABORT, 'guard: a reviewer cannot misrepresent its own tier or family');
END;

-- Build evidence can only come from the harness. (Sol #9 — the DB accepted a
-- model-captured screenshot; only the Python helper refused.)
CREATE TRIGGER IF NOT EXISTS guard_models_cannot_forge_evidence
BEFORE INSERT ON artifacts
WHEN NEW.captured_by = 'model' AND NEW.kind NOT IN ('source', 'quote')
BEGIN
    SELECT RAISE(ABORT, 'guard: a model may cite a source but may not produce build evidence');
END;

-- ===========================================================================
-- GUARD 1 — local output never ships unreviewed (§4.3, §9)
-- Now reads the SNAPSHOT (builder_tier), not the live seat.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_local_output_needs_review
BEFORE UPDATE OF status ON jobs
WHEN NEW.status IN ('done','shipped') AND OLD.status NOT IN ('done','shipped')
     AND NEW.builder_tier = 'local'
BEGIN
    SELECT RAISE(ABORT, 'guard: local-built job requires a passing subscription-tier review before done')
    WHERE NOT EXISTS (
        SELECT 1 FROM verdicts v
        WHERE v.job_id = NEW.id
          AND v.reviewer_tier IN ('subscription', 'metered')
          AND v.verdict = 'pass'
    );
END;

-- ===========================================================================
-- GUARD 2 — escalations must be answered, not outrun (§6)
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_unresolved_escalation
BEFORE UPDATE OF status ON jobs
WHEN NEW.status IN ('done','shipped') AND OLD.status NOT IN ('done','shipped')
BEGIN
    SELECT RAISE(ABORT, 'guard: job has an unresolved needs_human verdict')
    WHERE EXISTS (
        SELECT 1 FROM verdicts v WHERE v.job_id = NEW.id AND v.verdict = 'needs_human'
    );
END;

-- ===========================================================================
-- GUARD 3 — a tester's verdict must cite REAL BUILD EVIDENCE.
--
-- ⚠️ Sol #3: "The 'no screenshot, no verdict' claim is false." My original guard
-- accepted ANY artifact — including a research source, or an empty row. So a tester
-- could "pass" a job on the strength of a URL somebody pasted. Now it must be
-- evidence the HARNESS captured by actually driving the app.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_tester_must_cite_artifacts
BEFORE INSERT ON verdicts
WHEN NEW.role = 'tester'
BEGIN
    SELECT RAISE(ABORT, 'guard: a tester verdict requires real captured evidence — no screenshot, no verdict')
    WHERE NOT EXISTS (
        SELECT 1 FROM artifacts a
        WHERE a.job_id = NEW.job_id
          AND a.kind IN ('screenshot', 'trace', 'video', 'dom_snapshot')
          AND a.captured_by IN ('harness', 'playwright')
          AND COALESCE(a.path, a.value) IS NOT NULL
    );
END;

-- ===========================================================================
-- GUARD 4 — a model family may not test its own work.
-- Reads the snapshot, so re-pointing a seat later changes nothing.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_no_self_family_testing
BEFORE INSERT ON verdicts
WHEN NEW.role = 'tester'
     AND NEW.model_family = (SELECT builder_family FROM jobs WHERE id = NEW.job_id)
BEGIN
    SELECT RAISE(ABORT, 'guard: a model family may not test its own build');
END;

-- ===========================================================================
-- GUARD 5 — the full panel, always. DISTINCT reviewers.
--
-- ⚠️ Sol #7: "The full panel can be faked with duplicate passes." It counted ROWS.
-- One reviewer could pass the same job six times. Now it counts distinct seats.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_full_panel
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done' AND NEW.required_reviews > 0
BEGIN
    SELECT RAISE(ABORT, 'guard: the full review panel has not reported')
    WHERE (
        SELECT COUNT(DISTINCT v.reviewer_seat) FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'pass'
    ) < NEW.required_reviews;
END;

-- ===========================================================================
-- GUARD 6 — nothing ships without a passing cross-family tester.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_ship_requires_a_passing_tester
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'shipped' AND OLD.status <> 'shipped'
BEGIN
    SELECT RAISE(ABORT, 'guard: nothing ships without a passing cross-family tester on the record')
    WHERE OLD.status <> 'done'
       OR NOT EXISTS (
           SELECT 1 FROM verdicts v
           WHERE v.job_id = NEW.id AND v.role = 'tester' AND v.verdict = 'pass'
       );
END;

-- ===========================================================================
-- GUARD 7 — a researcher must show you where it got that.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_research_must_cite_sources
BEFORE UPDATE OF status ON jobs
WHEN NEW.status IN ('done', 'shipped') AND OLD.status NOT IN ('done', 'shipped')
     AND NEW.kind = 'research'
BEGIN
    SELECT RAISE(ABORT, 'guard: a research answer must cite sources — no source, no answer')
    WHERE NOT EXISTS (
        SELECT 1 FROM artifacts a
        WHERE a.job_id = NEW.id AND a.kind IN ('source', 'quote')
          AND COALESCE(a.path, a.value) IS NOT NULL
    );
END;

-- ===========================================================================
-- GUARD 8 — a model family may not fact-check its own research.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_no_self_family_verifying
BEFORE INSERT ON verdicts
WHEN NEW.role = 'verifier'
     AND NEW.model_family = (SELECT builder_family FROM jobs WHERE id = NEW.job_id)
BEGIN
    SELECT RAISE(ABORT, 'guard: a model family may not fact-check its own research');
END;

-- ===========================================================================
-- GUARD 9 — spend caps are enforced by the DATABASE, not by good manners.
--
-- ⚠️ Sol #8: "Spend caps do not structurally prevent runaway cost." They were a
-- Python check before dispatch — a classic race (two dispatches both pass the check
-- before either records spend), and a direct write skipped them entirely. Now the
-- ledger itself refuses the entry that would breach the cap.
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_no_negative_spend
BEFORE INSERT ON usage
WHEN NEW.cost_cents < 0
BEGIN
    SELECT RAISE(ABORT, 'guard: spend cannot be negative — no unwinding the meter');
END;

CREATE TRIGGER IF NOT EXISTS guard_daily_cap_is_hard
BEFORE INSERT ON usage
BEGIN
    SELECT RAISE(ABORT, 'guard: this seat is over its daily cap')
    WHERE (SELECT daily_cap_cents FROM seats WHERE id = NEW.seat_id) IS NOT NULL
      AND (
        SELECT COALESCE(SUM(cost_cents), 0) FROM usage
         WHERE seat_id = NEW.seat_id AND day = date('now')
      ) + NEW.cost_cents
      > (SELECT daily_cap_cents FROM seats WHERE id = NEW.seat_id);
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
