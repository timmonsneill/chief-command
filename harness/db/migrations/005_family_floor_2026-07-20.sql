-- Migration 005 — the family floor becomes a GUARD, not a comment (task #10).
--
-- Sol's #10 design gate found the gauntlet's single most important property —
-- that a DIFFERENT model family reviewed the work — was enforced NOWHERE in the
-- database. `min_model_families = 2` lived in a config file and a comment; the
-- schema counted distinct SEATS (guard_full_panel) and required a higher TIER
-- (guard_local_output_needs_review), but never counted FAMILIES. So the property
-- this whole project exists to guarantee rested on Python being correct.
--
-- Now the DB refuses ->done unless at least `required_review_families` DISTINCT
-- families have a PASSING reviewer verdict on the CURRENT version. The floor is
-- snapshotted per job at dispatch (like required_reviews) and cannot be lowered.
--
-- RECOVERY-SAFE (Sol re-gate, 2026-07-20): an earlier edit to schema.sql, re-run by
-- init_db() on server restart, installed these two triggers WITHOUT the column they
-- reference — jamming every status change on the live DB. So this migration DROPs the
-- triggers first, adds the column, backfills in-flight jobs to the real floor, then
-- recreates the triggers — all in one transaction. Runs once, on a pre-005 DB.

BEGIN;

DROP TRIGGER IF EXISTS guard_family_floor;
DROP TRIGGER IF EXISTS guard_family_floor_is_fixed;

ALTER TABLE jobs ADD COLUMN required_review_families INTEGER NOT NULL DEFAULT 0;

-- Terminal rows stay 0 (history is history). Jobs still IN FLIGHT get the real floor so
-- they can't slip to done under the old one-family rule (Sol: don't grandfather active
-- work). They park until a real cross-family panel can satisfy the floor — the safe wait.
UPDATE jobs SET required_review_families = 2
 WHERE status NOT IN ('shipped', 'failed', 'cancelled', 'done');

-- The floor cannot be lowered after dispatch (mirrors guard_panel_size_is_fixed).
CREATE TRIGGER guard_family_floor_is_fixed
BEFORE UPDATE OF required_review_families ON jobs
WHEN OLD.required_review_families > 0 AND NEW.required_review_families < OLD.required_review_families
BEGIN
    SELECT RAISE(ABORT, 'guard: the family floor cannot be lowered after dispatch');
END;

-- The floor itself: >= N distinct families with a PASSING reviewer verdict on THIS
-- version. Counts families (not seats, not roles other than reviewer), version-matched
-- so moving the code voids old passes. Fails closed: too few families -> parked.
CREATE TRIGGER guard_family_floor
BEFORE UPDATE OF status ON jobs
WHEN NEW.status = 'done' AND OLD.status <> 'done' AND NEW.required_review_families > 0
BEGIN
    SELECT RAISE(ABORT, 'guard: fewer model families reviewed this than required')
    WHERE (
        SELECT COUNT(DISTINCT v.model_family) FROM verdicts v
        WHERE v.job_id = NEW.id AND v.verdict = 'pass' AND v.role = 'reviewer'
          AND v.reviewed_version IS NEW.head_version
    ) < NEW.required_review_families;
END;

COMMIT;
