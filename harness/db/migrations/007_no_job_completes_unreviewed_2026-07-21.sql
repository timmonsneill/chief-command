-- Migration 007 — two holes under the whole review system (found by the cross-model
-- review of task #10, reproduced live before fixing).
--
-- ── HOLE 1: a job with no requirements had no requirements to fail ──
--
-- required_reviews and required_review_families default to 0, and BOTH completion
-- guards were written `WHEN NEW.required_* > 0`. So a job whose requirements were never
-- stamped sailed to 'done' with zero verdicts — not by defeating a guard, but because
-- no guard fired. The only thing setting those numbers was Python in dispatch(), which
-- means gauntlet.py's claim that "the database is the boundary, not this file" was not
-- true of the most basic question of all: did ANY review happen?
--
-- Reproduced: create_job -> in_progress -> review -> done, no verdicts, no complaint.
--
-- Now the floor is unconditional. Reaching 'done' requires at least one passing
-- reviewer of the current version and at least one model family, whatever the row says
-- about itself. A job that wants more still needs more.
--
-- ── HOLE 2: a model could be counted as its own second opinion ──
--
-- The family floor counted DISTINCT families among the passing reviewers — including
-- the builder's own. So a gpt seat could build it, a gpt seat could pass it, and that
-- gpt pass counted as one of the two required "different minds."
--
-- Note what is NOT being changed. test_jobs.py records a deliberate decision that a
-- same-family reviewer is ALLOWED — it is a weaker signal, not a forbidden one, and
-- banning it outright would deadlock the current setup (a claude-built job with only
-- claude and gpt reviewers available could never clear a floor of two). That decision
-- stands. What changes is that such a review no longer COUNTS toward diversity.
--
-- The floor now asks the question it was always meant to ask: how many minds OTHER
-- THAN THE AUTHOR'S looked at this?

BEGIN;

DROP TRIGGER IF EXISTS guard_full_panel;
CREATE TRIGGER guard_full_panel
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done'
BEGIN
    SELECT RAISE(ABORT, 'guard: the full review panel has not reported')
    WHERE (
        SELECT COUNT(DISTINCT v.reviewer_seat) FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'pass' AND v.role = 'reviewer'
          AND v.reviewed_version IS NEW.head_version
    ) < MAX(NEW.required_reviews, 1);      -- never zero: unstamped is not unrequired
END;

-- An earlier draft of this migration banned same-family review outright. It ran against
-- the live database before the deadlock was spotted, so drop it explicitly rather than
-- assume it was never there.
DROP TRIGGER IF EXISTS guard_no_self_family_reviewing;

DROP TRIGGER IF EXISTS guard_family_floor;
CREATE TRIGGER guard_family_floor
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done'
BEGIN
    SELECT RAISE(ABORT, 'guard: fewer model families reviewed this than required')
    WHERE (
        SELECT COUNT(DISTINCT v.model_family) FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'pass' AND v.role = 'reviewer'
          AND v.reviewed_version IS NEW.head_version
          AND v.model_family <> NEW.builder_family   -- the author is not a second opinion
    ) < MAX(NEW.required_review_families, 1);
END;

-- ── The gatekeeper's own log ──
--
-- Every note it writes today hangs off a JOB, and `events.job_id` is NOT NULL. A deploy
-- isn't necessarily about a job, so both its grants AND its refusals were being dropped
-- on the floor — for the one verb where "say it out loud" matters most. A gatekeeper
-- whose refusals can vanish is indistinguishable from one that was never asked.
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

-- The log is a record of what was asked and answered. Records are not retractable —
-- same reasoning as verdicts.
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

COMMIT;
