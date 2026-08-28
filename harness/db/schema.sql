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
    -- ═══════════════════════════════════════════════════════════════════════
    -- MODEL TIERS. Owner (2026-07-14):
    --     "If we do highest model every time for every build, on autonomous work,
    --      Claude and ChatGPT are gonna bottom out. I need us to build in model
    --      tiering as well."
    --
    -- He's right, and it's the difference between a system that runs for a month and
    -- one that dies on Thursday. Rate limits are the binding constraint on autonomous
    -- work — NOT money (Claude and OpenAI are flat-rate). You cannot buy your way out
    -- of a weekly cap. You can only spend it wisely.
    --
    -- So every seat carries THREE models, and the harness picks by what the work
    -- actually deserves:
    --
    --   light     the cheap one. Boilerplate, scaffolding, small fixes, quick answers.
    --   standard  the default. Most real work.
    --   heavy     the top of the line. Reserved, and it has to be EARNED.
    --
    -- Nothing gets `heavy` by default. It's earned by: an explicit ask ("think hard"),
    -- a risky area (auth, money, data), a repeat failure, or a major decision. See
    -- tiering.py — the whole point is that the escalation is a rule, not a vibe.
    -- ═══════════════════════════════════════════════════════════════════════
    model_light    TEXT,     -- e.g. claude-haiku / gpt-5.6-luna
    model_standard TEXT,     -- e.g. claude-sonnet / gpt-5.6-terra
    model_heavy    TEXT,     -- e.g. claude-opus / gpt-5.6-sol  (EARNED, never default)
    effort_light    TEXT DEFAULT 'low',
    effort_standard TEXT DEFAULT 'medium',
    effort_heavy    TEXT DEFAULT 'high',

    -- CAPS ARE PER-ROLE, not just per-seat. Owner's call (2026-07-13):
    --     "it can review as much as it wants more or less, but shouldn't build as
    --      much as claude and chat"
    --
    -- The economics say the same thing. Claude and OpenAI are FLAT-RATE — a build on
    -- those seats costs nothing marginal, it's already paid for. Grok is METERED, so
    -- every build is real money. And builds are the expensive kind of work (token-heavy)
    -- while reviews are cheap (read-heavy, ~1/10 the tokens).
    --
    -- So: BUILD on the seats you've already bought. REVIEW on the metered one, because
    -- reviewing is cheap — and reviewing is what Grok is actually best at anyway. Its
    -- value was never being the strongest coder. It's being a DIFFERENT MIND.
    --
    -- NULL = uncapped.
    daily_cap_cents        INTEGER,   -- total across all roles (the hard ceiling)
    build_cap_cents        INTEGER,   -- ration the expensive work
    review_cap_cents       INTEGER,   -- reviewing is cheap; be generous
    enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    notes         TEXT
);

-- ---------------------------------------------------------------------------
-- Projects: the top-level thing work belongs to. Each has its OWN memory.
--
-- Owner: "ability to see a projects tab and each project has its own memory,
-- timeline/planning."
--
-- This matters more than it looks. Memory that isn't scoped to a project leaks:
-- lessons from the EMR bleed into the harness, conventions from one codebase get
-- applied to another. Chief already keeps per-AGENT memory; this is per-PROJECT,
-- and the two are different axes. Riggs knows how HE works; the project knows how
-- IT works.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,          -- 'chief', 'arch', 'jess'
    name          TEXT NOT NULL,
    repo_path     TEXT,
    description   TEXT,
    -- Where this project's own memory lives. Loaded into context for any job on it.
    memory_dir    TEXT,
    color         TEXT,                      -- the UI reads this
    archived      INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The REAL projects, seeded so Chief knows them on ANY machine (a fresh DB or the Mac
-- Studio), not just wherever a row happened to be typed. Without this, `projects` is
-- empty on a new box and Chief goes back to improvising what Neill is working on — the
-- exact bug this seed exists to kill. OR IGNORE so it never clobbers live edits.
--
-- repo_path: Arch is deliberately NULL. Chief may read Arch's NOTES (memory), but the
-- fleet must never be pointed at its code or its patient data (Decision C, PHI). Dispatch
-- does not route by repo_path today; if it ever learns to, THAT is where the guard goes.
--
-- memory_dir is NOT seeded here on purpose: it's the one genuinely machine-specific value
-- (where this box keeps the project's notes), so it's set as local config per machine —
-- keeping this file hardware-agnostic (rule 4).
INSERT OR IGNORE INTO projects (id, name, repo_path, description, color) VALUES
  ('chief', 'Chief Command', '~/code-projects/chief-command',
   'The thing you talk to. This.', '#2E9BFF'),
  ('jess', 'Jess', '~/code-projects/personal-assist',
   'Personal assistant. Connects to Chief over the tailnet later.', '#A78BFA'),
  ('arch', 'Arch (Arch to Freedom EMR)', NULL,
   'The records system for the addiction-recovery facility — staff, beds, roles, approvals, the voice assistant Archie. Voice-first. Held at arm''s length here on purpose: Chief can read its notes but the fleet does not touch its code or its patient data.',
   '#22c55e');

-- Project memory: facts that belong to the PROJECT, not to an agent and not to a job.
-- "The API returns snake_case." "Never touch the billing table directly."
CREATE TABLE IF NOT EXISTS project_memory (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL DEFAULT 'lesson'
                  CHECK (kind IN ('lesson', 'convention', 'constraint', 'decision', 'gotcha')),
    fact          TEXT NOT NULL,
    -- Where it came from, so a wrong lesson can be traced and killed.
    learned_from  INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pmem_project ON project_memory(project_id);

-- The plan. What's coming, in order. This is the timeline the UI draws.
CREATE TABLE IF NOT EXISTS plan_items (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    detail        TEXT,
    phase         TEXT,                      -- 'phase 1', 'phase 2'…
    status        TEXT NOT NULL DEFAULT 'planned'
                  CHECK (status IN ('planned', 'active', 'blocked', 'done', 'dropped')),
    blocked_on    TEXT,                      -- plain English. "waiting on Neill to log in"
    job_id        INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    position      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_plan_project ON plan_items(project_id, position);

-- ---------------------------------------------------------------------------
-- Todos: the checklist that belongs to a PROJECT, so it stops living in a
-- terminal window and following nobody. Owner's annoyance, verbatim: "right now I
-- have persistent todos that has to jump from window to window, which is annoying."
--
-- `owner_only` marks the items only Neill can do (rotate a key, flip a setting) —
-- the ones the fleet must never quietly tick off on his behalf.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS todos (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- Sub-category WITHIN a project, so a project can hold several running lists
    -- ("Security", "Build", "My tasks"). Owner: "ideally we can create a sub
    -- category under each project as well." NULL / '' folds into a "General" list.
    section       TEXT,
    text          TEXT NOT NULL,
    done          INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0, 1)),
    owner_only    INTEGER NOT NULL DEFAULT 0 CHECK (owner_only IN (0, 1)),
    position      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    done_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_todos_project ON todos(project_id, section, position, id);

-- ---------------------------------------------------------------------------
-- Attachments: images and files Neill drops in — a screenshot of a bug, a mockup,
-- a document — pinned to a project or a specific job. The bytes live on disk
-- (gitignored); this row is how the UI finds them. captured_by is always the owner
-- here; agent-produced evidence is the `artifacts` table, which has its own guards.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS attachments (
    id            INTEGER PRIMARY KEY,
    project_id    TEXT REFERENCES projects(id) ON DELETE SET NULL,
    job_id        INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    filename      TEXT NOT NULL,             -- the original name, for display
    stored_path   TEXT NOT NULL,             -- where the bytes actually live
    kind          TEXT NOT NULL CHECK (kind IN ('image', 'file')),
    size_bytes    INTEGER,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_attachments_project ON attachments(project_id);
CREATE INDEX IF NOT EXISTS idx_attachments_job ON attachments(job_id);

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

    project_id    TEXT REFERENCES projects(id) ON DELETE SET NULL,

    -- Which tier this job actually ran at, and WHY. The 'why' matters: if we're
    -- burning heavy tier on boilerplate, this column is how we find out.
    tier          TEXT NOT NULL DEFAULT 'standard'
                  CHECK (tier IN ('light', 'standard', 'heavy')),
    tier_reason   TEXT,

    builder_seat  TEXT NOT NULL REFERENCES seats(id),
    -- SNAPSHOTTED AT CREATION. Sol (cross-family review, 2026-07-13) found that
    -- guards read the builder's tier/family LIVE from `seats`, so re-tiering a local
    -- seat to 'subscription' retroactively legitimized its old unreviewed work.
    -- Freeze it here. What a seat is TODAY cannot change what it WAS.
    builder_tier   TEXT NOT NULL DEFAULT 'local'
                   CHECK (builder_tier IN ('local','subscription','metered')),
    builder_family TEXT NOT NULL DEFAULT 'unknown',

    -- WHAT SHAPE the reviewed bundle is (task #9, migration 008). 'text' = one
    -- file's content (the local model's path, unchanged). 'diff' = a code
    -- builder's `git diff <merge-base main>..<tip>` — a different review AND
    -- merge contract. Stamped at dispatch from the builder seat's provider,
    -- frozen by guard_builder_identity_is_frozen below — never inferred later
    -- from a seat's live (mutable) provider column.
    bundle_kind   TEXT NOT NULL DEFAULT 'text' CHECK (bundle_kind IN ('text', 'diff')),

    run_id        TEXT,                      -- OpenClaw sessions_spawn run id
    session_key   TEXT,                      -- agent:<id>:subagent:<uuid>

    -- Git as audit trail (§7). Worktree-per-job, main untouched until merged.
    branch        TEXT,
    worktree      TEXT,

    -- THE VERSION UNDER REVIEW (Sol build gate 3, 2026-07-20). The exact content
    -- hash the builder is putting forward — a git commit id. Reviews bind to this:
    -- a verdict only counts toward completion while its reviewed_version matches.
    -- The moment the builder changes the code, this changes, and every earlier
    -- approval silently stops counting. Sol called the alternative the most
    -- dangerous flaw in the system: "believable green checks on code nobody
    -- reviewed." The gatekeeper's job at push time is to verify the code leaving
    -- the building IS this hash — the DB proves the chain, git proves the content.
    head_version  TEXT,

    -- 'done'    = the gauntlet passed. The machine is satisfied.
    -- 'shipped' = merged into the main line by the GATEKEEPER (Decision D, 2026-07-17:
    --             the gauntlet IS the approval; Neill gates production DEPLOYS, not
    --             merges). Guard 6 still requires a cross-family tester on the record.
    -- (Earlier wording — "only Neill can move this" — predates Decision D.)
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

    -- How many DISTINCT model families must have a passing review before done. This is
    -- the floor "full set, always" doesn't itself guarantee: a full panel could still be
    -- one family wearing three hats. Set at dispatch from the gauntlet's
    -- min_model_families. Snapshotted and un-lowerable, like required_reviews. (task #10)
    required_review_families INTEGER NOT NULL DEFAULT 0,

    result        TEXT,                      -- final summary / diff ref
    error         TEXT,

    -- Voice wants a spoken-length answer; the text log keeps the detail (§5.3).
    spoken_summary TEXT,

    attempts      INTEGER NOT NULL DEFAULT 0,
    parent_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,

    -- DUPLICATE PROTECTION. A retry — a double-tap, a dropped connection, the voice
    -- re-sending — must not start the same work twice. The caller stamps a key; a
    -- second dispatch with the same key returns the existing job instead of spawning
    -- a new one. NULL = no key given (nothing to dedupe against). The unique index
    -- below lets many NULLs coexist but forbids two rows sharing a real key.
    dispatch_key  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_run     ON jobs(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dispatch_key ON jobs(dispatch_key)
    WHERE dispatch_key IS NOT NULL;

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
    -- WHAT, exactly, did this reviewer look at? Snapshotted at write time. A verdict
    -- whose version no longer matches the job's head_version is history, not
    -- approval — it stops counting the moment the code moves on. This is the other
    -- half of the review-to-version chain.
    reviewed_version TEXT,
    verdict       TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'needs_human')),
    severity      TEXT CHECK (severity IN ('p0', 'p1', 'p2', 'p3')),
    summary       TEXT,
    detail        TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_verdicts_job ON verdicts(job_id);

-- ═══════════════════════════════════════════════════════════════════════════════
-- CAPABILITIES — what an agent is PHYSICALLY ALLOWED to do.
--
-- This exists because Sol reviewed the architecture and demolished the previous
-- safety story. The plan had been: the voice can act fast, and Chief watches and
-- kills anything dumb. Sol's verdict:
--
--     "'Builders take minutes' measures how long they take to FINISH. What matters
--      is how long they take to do their FIRST DAMAGING THING. That can be seconds.
--      Chief and the builder would be RACING. There is no guarantee Chief wins."
--
--     "A fast supervisor is still only a fast witness if the builder can act first."
--
--     "A smarter model is not a security boundary."
--
-- He's right, and it's the difference between a bank with a vault and a bank with an
-- attentive guard. You do not stop a robbery by watching carefully.
--
-- SO: agents get NO dangerous powers. Not "Chief will stop them" — THEY CANNOT.
-- Reading code, writing code in their own worktree, running tests: always fine, and
-- that's 95% of the work. Everything that touches the real world is denied by
-- default and needs a signed, single-use permission slip for that exact act.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS capabilities (
    seat_id       TEXT NOT NULL REFERENCES seats(id) ON DELETE CASCADE,
    capability    TEXT NOT NULL CHECK (capability IN (
                      -- SAFE. Granted to every builder. This is the actual job.
                      'read_code', 'write_worktree', 'run_tests', 'open_pr',
                      -- DANGEROUS. Denied to everyone by default. Each needs a
                      -- fresh, single-use approval naming the exact action.
                      'touch_production', 'delete_data', 'run_migration',
                      'deploy', 'merge_to_main', 'force_push',
                      'read_secrets', 'send_external', 'spend_money'
                  )),
    PRIMARY KEY (seat_id, capability)
);

-- The safe set. Everything an agent needs to actually build software, and nothing
-- that can hurt you. Note what ISN'T here — that's the whole point.
CREATE VIEW IF NOT EXISTS safe_capabilities AS
SELECT 'read_code' AS capability UNION ALL SELECT 'write_worktree'
UNION ALL SELECT 'run_tests' UNION ALL SELECT 'open_pr';

-- ═══════════════════════════════════════════════════════════════════════════════
-- APPROVALS — a signed, single-use permission slip for ONE exact dangerous act.
--
-- Sol on why a "yes" cannot be trusted on its own:
--
--     Chief: "First make a backup, then remove the old accounts."
--     Neill: "Yes — skip the first one."
--
--     "That LOOKS like a confirmation, but it changes the safe plan into a dangerous
--      one. Sending it directly recreates the original classification problem."
--
-- So an approval is not a word. It is a ROW: a numbered, one-time permission for a
-- specific action, with the consequence written out in plain English and READ BACK to
-- Neill before he can agree. It expires the moment the plan changes.
--
--     "A bare 'yes' should never approve something that was not just read back
--      exactly." — which also handles the fact that a car is a terrible place to be
--      understood correctly.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS approvals (
    id            INTEGER PRIMARY KEY,
    job_id        INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    capability    TEXT NOT NULL,            -- the ONE dangerous thing this permits
    -- Exactly what will happen. Not a category — the actual act.
    action        TEXT NOT NULL,            -- "delete 4,200 accounts inactive since 2024-01-01"
    -- What Chief SAID OUT LOUD to Neill before he agreed. If this is empty, nothing
    -- was read back, and a 'yes' means nothing.
    read_back     TEXT NOT NULL,
    -- Is this undoable, and how? Sol: "Anything irreversible needs a tested recovery
    -- method BEFORE it starts."
    reversible    INTEGER NOT NULL DEFAULT 0 CHECK (reversible IN (0,1)),
    recovery      TEXT,                     -- "restore from the snapshot taken at 09:14"

    granted_by    TEXT NOT NULL DEFAULT 'owner' CHECK (granted_by = 'owner'),
    granted_at    TEXT,
    expires_at    TEXT NOT NULL,            -- short. Minutes, not hours.
    used_at       TEXT,                     -- single use. Once spent, it's spent.
    revoked_at    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_approvals_job ON approvals(job_id);

-- ═══════════════════════════════════════════════════════════════════════════════
-- GUARD 12 — a dangerous act needs a live, unused, read-back approval.
-- No approval, no action. Not "Chief said it was fine" — a signed slip, or nothing.
-- ═══════════════════════════════════════════════════════════════════════════════
CREATE VIEW IF NOT EXISTS live_approvals AS
SELECT * FROM approvals
 WHERE granted_at IS NOT NULL
   AND used_at    IS NULL
   AND revoked_at IS NULL
   AND expires_at > datetime('now')
   AND TRIM(read_back) <> '';

-- An approval may never be BORN granted. (Sol, round 3 — verified 2026-07-14.)
--
-- Every approval guard fired on UPDATE OF granted_at only — the exact "born done" hole
-- Sol found on the jobs table in round 1, never applied here. Confirmed with a live
-- exploit: an INSERT with granted_at already set, reversible=0 and no recovery plan sailed
-- straight into the live_approvals view. An approval must be born ungranted and become
-- granted through an UPDATE, where the read-back and recovery guards below actually fire.
CREATE TRIGGER IF NOT EXISTS guard_no_approval_is_born_granted
BEFORE INSERT ON approvals
WHEN NEW.granted_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'guard: an approval cannot be created already granted — it must be granted through a read-back');
END;

-- An approval cannot be granted without something having been read back.
CREATE TRIGGER IF NOT EXISTS guard_no_approval_without_readback
BEFORE UPDATE OF granted_at ON approvals
WHEN NEW.granted_at IS NOT NULL AND TRIM(COALESCE(NEW.read_back, '')) = ''
BEGIN
    SELECT RAISE(ABORT, 'guard: nothing was read back to him — a yes to nothing is not a yes');
END;

-- An irreversible act cannot be approved without a recovery plan.
-- Sol: "'Kill' must never be presented as 'undo.'"
CREATE TRIGGER IF NOT EXISTS guard_irreversible_needs_a_way_back
BEFORE UPDATE OF granted_at ON approvals
WHEN NEW.granted_at IS NOT NULL AND NEW.reversible = 0
     AND TRIM(COALESCE(NEW.recovery, '')) = ''
BEGIN
    SELECT RAISE(ABORT, 'guard: this cannot be undone and has no way back — not without a recovery plan');
END;

-- Single use. A permission slip is spent when it is used.
CREATE TRIGGER IF NOT EXISTS guard_an_approval_is_used_once
BEFORE UPDATE OF used_at ON approvals
WHEN OLD.used_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'guard: that permission was already spent');
END;

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
    -- 'skipped' is load-bearing (task #10): the record needs a word for something that
    -- did NOT happen. A reviewer that was capped out or couldn't run must leave a mark,
    -- or a panel that quietly shrank looks identical to a full one. (Migration 006.)
    kind          TEXT NOT NULL CHECK (kind IN (
                      'dispatched', 'thinking', 'read', 'edit', 'write',
                      'command', 'test_run', 'browse', 'verdict', 'done', 'error',
                      'skipped'
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
    -- What kind of work this spend was. Lets us ration building separately from
    -- reviewing — the whole point of the per-role caps above.
    role          TEXT NOT NULL DEFAULT 'build'
                  CHECK (role IN ('build', 'review', 'test', 'research', 'voice')),
    day           TEXT NOT NULL DEFAULT (date('now')),
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cents    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_usage_seat_day ON usage(seat_id, day);
CREATE INDEX IF NOT EXISTS idx_usage_month ON usage(day);

-- ═══════════════════════════════════════════════════════════════════════════
-- THE MONEY. Owner (2026-07-14): "$100/mo limit, notify me at $50."
--
-- TWO LAYERS, deliberately:
--
--   1. OpenAI's OWN monthly budget cap ($100), set in their dashboard. That is the
--      REAL ceiling — it physically stops them serving requests, and no bug in this
--      codebase can defeat it. It is also a terrible way to find out, because it
--      cuts you off mid-sentence.
--
--   2. THIS. A softer, earlier layer that warns at $50 and refuses to spend past a
--      budget we set ourselves — so we stop gracefully, say why, and never hit
--      their wall at all.
--
-- Belt and braces. The dashboard cap is the thing that saves you if I'm wrong.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS budget (
    id              INTEGER PRIMARY KEY CHECK (id = 1),   -- exactly one row
    monthly_cap_cents  INTEGER NOT NULL DEFAULT 10000,    -- $100
    warn_at_cents      INTEGER NOT NULL DEFAULT 5000,     -- $50
    warned_this_month  TEXT,                              -- 'YYYY-MM' so we warn once
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO budget (id, monthly_cap_cents, warn_at_cents) VALUES (1, 10000, 5000);

-- The month's spend, all seats. This is the number that matters.
CREATE VIEW IF NOT EXISTS month_spend AS
SELECT
    COALESCE(SUM(cost_cents), 0)                                   AS spent_cents,
    (SELECT monthly_cap_cents FROM budget WHERE id = 1)            AS cap_cents,
    (SELECT warn_at_cents     FROM budget WHERE id = 1)            AS warn_cents
FROM usage
WHERE strftime('%Y-%m', day) = strftime('%Y-%m', 'now');

-- ═══════════════════════════════════════════════════════════════════════════
-- GUARD 11 — the month's budget is hard. It refuses the entry that would breach it.
--
-- Same principle as every other guard here: this is not a policy an agent is asked
-- to respect, it is a write that fails.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TRIGGER IF NOT EXISTS guard_monthly_budget_is_hard
BEFORE INSERT ON usage
BEGIN
    SELECT RAISE(ABORT, 'guard: this would blow the monthly budget')
    WHERE (SELECT spent_cents FROM month_spend) + NEW.cost_cents
        > (SELECT monthly_cap_cents FROM budget WHERE id = 1);
END;

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

-- A job may never be BORN finished. (Sol, round 1, #1 — the worst one.)
CREATE TRIGGER IF NOT EXISTS guard_no_job_is_born_done
BEFORE INSERT ON jobs
WHEN NEW.status IN ('done', 'shipped')
BEGIN
    SELECT RAISE(ABORT, 'guard: a job cannot be created already finished — it must earn it');
END;

-- A job may not LIE ABOUT WHO BUILT IT AT BIRTH. (Sol, round 2, #1 — critical.)
--
-- I froze the builder's identity AFTER creation, so it couldn't be rewritten. But I
-- never checked it AT creation. Sol: "A direct database write can label local work as
-- subscription work, then complete it without the required higher-quality review. The
-- identity is frozen only after creation; the initial lie is never caught."
--
-- Freezing a lie just makes it a permanent lie.
CREATE TRIGGER IF NOT EXISTS guard_builder_identity_must_be_real
BEFORE INSERT ON jobs
WHEN NEW.builder_tier   <> (SELECT tier   FROM seats WHERE id = NEW.builder_seat)
  OR NEW.builder_family <> (SELECT family FROM seats WHERE id = NEW.builder_seat)
BEGIN
    SELECT RAISE(ABORT, 'guard: a job cannot misrepresent who built it');
END;

-- The builder's tier/family are snapshotted at creation and are then HISTORY.
-- (Sol #5, #6 — re-tiering a seat rewrote the past.)
CREATE TRIGGER IF NOT EXISTS guard_builder_identity_is_frozen
BEFORE UPDATE ON jobs
WHEN OLD.builder_seat <> NEW.builder_seat
  OR OLD.builder_tier <> NEW.builder_tier
  OR OLD.builder_family <> NEW.builder_family
  OR OLD.bundle_kind <> NEW.bundle_kind
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

-- ═══════════════════════════════════════════════════════════════════════════
-- THE REVIEW-TO-VERSION CHAIN (Sol build gate 3, 2026-07-20).
--
-- Sol's most dangerous flaw: "approve version A, builder changes it to B, the old
-- approval still counts — believable green checks on code nobody reviewed."
--
-- The fix is not another approval ceremony; it is arithmetic. A verdict carries the
-- version it reviewed. A job carries the version it is putting forward. Completion
-- guards only count verdicts whose versions MATCH — so moving the code voids the
-- approvals by construction, nothing needs to remember to revoke anything. And a
-- FAIL condemns the version it saw, not the job forever: fix the code, the version
-- changes, the old fail becomes history. (A fail with no recorded version blocks
-- everything — fail-closed, because it condemned we-don't-know-what.)
-- ═══════════════════════════════════════════════════════════════════════════

-- A versioned job accepts no unversioned verdicts. (Unversioned legacy jobs keep
-- their old semantics; the gatekeeper will not push anything unversioned anyway.)
CREATE TRIGGER IF NOT EXISTS guard_verdict_must_cite_what_it_reviewed
BEFORE INSERT ON verdicts
WHEN (SELECT head_version FROM jobs WHERE id = NEW.job_id) IS NOT NULL
     AND TRIM(COALESCE(NEW.reviewed_version, '')) = ''
BEGIN
    SELECT RAISE(ABORT, 'guard: this job is versioned — a verdict must say which version it reviewed');
END;

-- A build cannot finish without declaring WHICH code is finished.
CREATE TRIGGER IF NOT EXISTS guard_a_build_finishes_a_version
BEFORE UPDATE OF status ON jobs
WHEN NEW.status IN ('done', 'shipped') AND OLD.status NOT IN ('done', 'shipped')
     AND NEW.kind = 'build'
     AND TRIM(COALESCE(NEW.head_version, '')) = ''
BEGIN
    SELECT RAISE(ABORT, 'guard: a build cannot finish without naming the exact version that is finished');
END;

-- Once finished, WHAT finished is history. Rewriting head_version after done would
-- let approved-hash-A ship as hash-B while every record still looks green.
CREATE TRIGGER IF NOT EXISTS guard_finished_version_is_frozen
BEFORE UPDATE OF head_version ON jobs
WHEN OLD.status IN ('done', 'shipped') AND OLD.head_version IS NOT NEW.head_version
BEGIN
    SELECT RAISE(ABORT, 'guard: what version finished cannot be rewritten after the fact');
END;

-- A verdict cannot be DELETED. (Sol, round 3 — verified 2026-07-14.)
--
-- "Append-only" was enforced only against UPDATE, so a failing review that couldn't be
-- EDITED into a pass could simply be DELETED — after which guard_a_failing_review_stops_it
-- (which asks "does a fail EXIST?") saw nothing and let the job complete. Confirmed with a
-- live exploit: fail deleted, then done. A verdict is a fact; facts are not retractable.
CREATE TRIGGER IF NOT EXISTS guard_verdicts_cannot_be_deleted
BEFORE DELETE ON verdicts
BEGIN
    SELECT RAISE(ABORT, 'guard: a verdict is a fact on the record — it cannot be deleted');
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
          -- gate 3: a pass only counts for the version it actually reviewed
          AND v.reviewed_version IS NEW.head_version
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
-- The floor is UNCONDITIONAL (migration 007). It used to fire only when
-- required_reviews > 0 — so a job whose requirements were never stamped had no
-- requirements to fail, and reached 'done' with zero verdicts. Unstamped is not
-- unrequired.
CREATE TRIGGER IF NOT EXISTS guard_full_panel
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done'
BEGIN
    SELECT RAISE(ABORT, 'guard: the full review panel has not reported')
    WHERE (
        -- Sol, round 2, #5: this counted testers and fact-checkers as reviewers, so
        -- the panel could be "filled" by the wrong kinds of check entirely.
        SELECT COUNT(DISTINCT v.reviewer_seat) FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'pass' AND v.role = 'reviewer'
          -- gate 3: the panel must have reviewed THIS version, not an earlier one
          AND v.reviewed_version IS NEW.head_version
    ) < MAX(NEW.required_reviews, 1);
END;

-- ===========================================================================
-- GUARD 5b — the family floor (task #10). "Full set, always" counts SEATS; it does
-- not guarantee more than one MIND looked. Sol's #10 gate: min_model_families was
-- enforced nowhere in the DB — the gauntlet's core property rested on Python. Now
-- the DB refuses ->done without >= required_review_families DISTINCT passing families
-- on this version. Fails closed: too few families -> parked (never lowers the bar).
-- Snapshotted un-lowerable at dispatch (guard_family_floor_is_fixed).
-- ===========================================================================
CREATE TRIGGER IF NOT EXISTS guard_family_floor_is_fixed
BEFORE UPDATE OF required_review_families ON jobs
WHEN OLD.required_review_families > 0 AND NEW.required_review_families < OLD.required_review_families
BEGIN
    SELECT RAISE(ABORT, 'guard: the family floor cannot be lowered after dispatch');
END;

CREATE TRIGGER IF NOT EXISTS guard_family_floor
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done'
BEGIN
    SELECT RAISE(ABORT, 'guard: fewer model families reviewed this than required')
    WHERE (
        SELECT COUNT(DISTINCT v.model_family) FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'pass' AND v.role = 'reviewer'
          AND v.reviewed_version IS NEW.head_version
          -- The AUTHOR is not a second opinion. Same-family review is allowed (it is a
          -- weaker signal, not a forbidden one — see test_jobs.py) but it does not COUNT
          -- toward diversity, or "two different minds" could include the one that wrote
          -- it. (Migration 007.)
          AND v.model_family <> NEW.builder_family
    ) < MAX(NEW.required_review_families, 1);
END;

-- ---------------------------------------------------------------------------
-- The gatekeeper's own log (task #11). events.job_id is NOT NULL, and a deploy is not
-- necessarily about a job — so without this, a deploy's grants AND refusals had nowhere
-- to go and were silently dropped. Append-only, like verdicts: a record, not a draft.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gate_log (
    id          INTEGER PRIMARY KEY,
    verb        TEXT NOT NULL,
    subject     TEXT NOT NULL,
    granted     INTEGER NOT NULL CHECK (granted IN (0,1)),
    detail      TEXT NOT NULL,
    asked_by    TEXT NOT NULL,
    job_id      INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS guard_gate_log_is_append_only
BEFORE UPDATE ON gate_log
BEGIN
    SELECT RAISE(ABORT, 'guard: the gatekeeper log is a record, not a draft');
END;

CREATE TRIGGER IF NOT EXISTS guard_gate_log_cannot_be_deleted
BEFORE DELETE ON gate_log
BEGIN
    SELECT RAISE(ABORT, 'guard: the gatekeeper log cannot be deleted');
END;

-- ═══════════════════════════════════════════════════════════════════════════
-- GUARD 10 — A FAILING REVIEW STOPS THE JOB. (Sol, round 2, #5 — and this one is bad.)
--
-- My panel guard counted PASSES and never looked at FAILS. So three passes and one
-- serious failure satisfied it. A reviewer could scream that the thing was broken and
-- be outvoted by colleagues who hadn't noticed.
--
-- That is the opposite of what a gauntlet is FOR. One reviewer finding a real bug is
-- the whole point — it isn't a democracy, it's a set of independent smoke detectors.
-- Any one of them going off stops the job.
-- ═══════════════════════════════════════════════════════════════════════════
CREATE TRIGGER IF NOT EXISTS guard_a_failing_review_stops_it
BEFORE UPDATE OF status ON jobs
WHEN NEW.status IN ('done', 'shipped') AND OLD.status NOT IN ('done', 'shipped')
BEGIN
    SELECT RAISE(ABORT, 'guard: a reviewer failed this — it does not get outvoted')
    WHERE EXISTS (
        SELECT 1 FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'fail'
          -- gate 3: a fail condemns the VERSION it reviewed, not the job forever.
          -- A fail with no version condemned we-don't-know-what: it blocks
          -- everything. Same if the job itself is unversioned. Fail-closed.
          AND (NEW.head_version IS NULL
               OR v.reviewed_version IS NULL
               OR v.reviewed_version = NEW.head_version)
    );
END;

-- Sol, round 2, #6: an objection raised AFTER 'done' could still be outrun, because
-- the escalation guard only fired on the transition INTO done — not on done -> shipped.
CREATE TRIGGER IF NOT EXISTS guard_late_escalation_still_blocks_shipping
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'shipped' AND OLD.status <> 'shipped'
BEGIN
    SELECT RAISE(ABORT, 'guard: someone raised an objection — it has to be answered first')
    WHERE EXISTS (
        SELECT 1 FROM verdicts v
        WHERE v.job_id = NEW.id
          AND (v.verdict = 'needs_human'  -- an unanswered question blocks, always
               -- gate 3: a fail blocks the version it condemned (fail-closed when
               -- either side is unversioned)
               OR (v.verdict = 'fail'
                   AND (NEW.head_version IS NULL
                        OR v.reviewed_version IS NULL
                        OR v.reviewed_version = NEW.head_version)))
    );
END;

-- Sol, round 2, #4: evidence could be an EMPTY STRING. A path of '' satisfied
-- "COALESCE(path, value) IS NOT NULL" perfectly well.
CREATE TRIGGER IF NOT EXISTS guard_evidence_cannot_be_empty
BEFORE INSERT ON artifacts
WHEN TRIM(COALESCE(NEW.path, NEW.value, '')) = ''
BEGIN
    SELECT RAISE(ABORT, 'guard: evidence with nothing in it is not evidence');
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
             -- gate 3: the tester must have driven THIS version of the app
             AND v.reviewed_version IS NEW.head_version
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

-- Ration the EXPENSIVE work separately. A seat can be generous on reviewing and
-- stingy on building — which is exactly how you want a metered seat to behave when
-- you already pay flat-rate for two better builders.
CREATE TRIGGER IF NOT EXISTS guard_build_cap_is_hard
BEFORE INSERT ON usage
WHEN NEW.role = 'build'
BEGIN
    SELECT RAISE(ABORT, 'guard: this seat has used up its building budget for today — it can still review')
    WHERE (SELECT build_cap_cents FROM seats WHERE id = NEW.seat_id) IS NOT NULL
      AND (
        SELECT COALESCE(SUM(cost_cents), 0) FROM usage
         WHERE seat_id = NEW.seat_id AND day = date('now') AND role = 'build'
      ) + NEW.cost_cents
      > (SELECT build_cap_cents FROM seats WHERE id = NEW.seat_id);
END;

CREATE TRIGGER IF NOT EXISTS guard_review_cap_is_hard
BEFORE INSERT ON usage
WHEN NEW.role IN ('review', 'test')
BEGIN
    SELECT RAISE(ABORT, 'guard: this seat is over its reviewing budget for today')
    WHERE (SELECT review_cap_cents FROM seats WHERE id = NEW.seat_id) IS NOT NULL
      AND (
        SELECT COALESCE(SUM(cost_cents), 0) FROM usage
         WHERE seat_id = NEW.seat_id AND day = date('now') AND role IN ('review','test')
      ) + NEW.cost_cents
      > (SELECT review_cap_cents FROM seats WHERE id = NEW.seat_id);
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
