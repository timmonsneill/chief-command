-- Migration 002 — the review-to-version chain (Sol build gate 3).
--
-- Sol's most dangerous flaw: approve version A, builder changes it to B, the old
-- approval still counts — "believable green checks on code nobody reviewed."
--
-- Jobs now carry head_version (the exact content hash being put forward); verdicts
-- carry reviewed_version (what the reviewer actually looked at). Completion guards
-- only count verdicts whose version MATCHES, so moving the code voids approvals by
-- construction, and a fail condemns the version it saw instead of the job forever.
--
-- Existing rows keep NULL versions: job 1 shipped before versioning existed (its
-- record is history and stays untouched); job 2 cannot now complete until it gets a
-- version and version-matched reviews — which is correct, nobody reviewed a
-- recorded version of it.

BEGIN;

ALTER TABLE jobs ADD COLUMN head_version TEXT;
ALTER TABLE verdicts ADD COLUMN reviewed_version TEXT;

CREATE TRIGGER guard_verdict_must_cite_what_it_reviewed
BEFORE INSERT ON verdicts
WHEN (SELECT head_version FROM jobs WHERE id = NEW.job_id) IS NOT NULL
     AND TRIM(COALESCE(NEW.reviewed_version, '')) = ''
BEGIN
    SELECT RAISE(ABORT, 'guard: this job is versioned — a verdict must say which version it reviewed');
END;

CREATE TRIGGER guard_a_build_finishes_a_version
BEFORE UPDATE OF status ON jobs
WHEN NEW.status IN ('done', 'shipped') AND OLD.status NOT IN ('done', 'shipped')
     AND NEW.kind = 'build'
     AND TRIM(COALESCE(NEW.head_version, '')) = ''
BEGIN
    SELECT RAISE(ABORT, 'guard: a build cannot finish without naming the exact version that is finished');
END;

CREATE TRIGGER guard_finished_version_is_frozen
BEFORE UPDATE OF head_version ON jobs
WHEN OLD.status IN ('done', 'shipped') AND OLD.head_version IS NOT NEW.head_version
BEGIN
    SELECT RAISE(ABORT, 'guard: what version finished cannot be rewritten after the fact');
END;

-- The five completion guards, rebuilt with version matching. Executable text must
-- stay identical to schema.sql — the drift test compares them.

DROP TRIGGER guard_local_output_needs_review;
CREATE TRIGGER guard_local_output_needs_review
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

DROP TRIGGER guard_full_panel;
CREATE TRIGGER guard_full_panel
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done' AND NEW.required_reviews > 0
BEGIN
    SELECT RAISE(ABORT, 'guard: the full review panel has not reported')
    WHERE (
        -- Sol, round 2, #5: this counted testers and fact-checkers as reviewers, so
        -- the panel could be "filled" by the wrong kinds of check entirely.
        SELECT COUNT(DISTINCT v.reviewer_seat) FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'pass' AND v.role = 'reviewer'
          -- gate 3: the panel must have reviewed THIS version, not an earlier one
          AND v.reviewed_version IS NEW.head_version
    ) < NEW.required_reviews;
END;

DROP TRIGGER guard_a_failing_review_stops_it;
CREATE TRIGGER guard_a_failing_review_stops_it
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

DROP TRIGGER guard_late_escalation_still_blocks_shipping;
CREATE TRIGGER guard_late_escalation_still_blocks_shipping
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

DROP TRIGGER guard_ship_requires_a_passing_tester;
CREATE TRIGGER guard_ship_requires_a_passing_tester
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

COMMIT;
