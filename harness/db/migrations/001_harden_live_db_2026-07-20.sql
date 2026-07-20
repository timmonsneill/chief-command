-- Migration 001 — bring the LIVE database up to the hardened schema.sql
-- Sol build gate 1 (round 7): "migrate the live DB to the hardened rules."
--
-- Found by diffing the live DB against a fresh build of schema.sql on 2026-07-20:
--   1. guard_verdicts_cannot_be_deleted   MISSING — a failing review could be deleted,
--      then the job completed (Sol round 3, verified exploit).
--   2. guard_no_approval_is_born_granted  MISSING — an approval could be inserted
--      pre-granted, skipping the read-back guards (Sol round 3, verified exploit).
--   3. guard_full_panel                   STALE — live version counts any 'pass',
--      so testers/verifiers could fill the reviewer panel (Sol round 2, #5).
--   4. jobs.tier / jobs.tier_reason       MISSING — tiering has nowhere to record.
--   5. seats model_/effort_ tier columns  MISSING — same.
--
-- Additive ALTERs + trigger recreation only. No row is rewritten or re-inserted —
-- replaying history through the guards is exactly what the guards exist to prevent.
-- Backup taken first: harness/db/backups/chief.db.pre-hardening-2026-07-20

BEGIN;

ALTER TABLE jobs ADD COLUMN tier TEXT NOT NULL DEFAULT 'standard'
    CHECK (tier IN ('light', 'standard', 'heavy'));
ALTER TABLE jobs ADD COLUMN tier_reason TEXT;

ALTER TABLE seats ADD COLUMN model_light    TEXT;
ALTER TABLE seats ADD COLUMN model_standard TEXT;
ALTER TABLE seats ADD COLUMN model_heavy    TEXT;
ALTER TABLE seats ADD COLUMN effort_light    TEXT DEFAULT 'low';
ALTER TABLE seats ADD COLUMN effort_standard TEXT DEFAULT 'medium';
ALTER TABLE seats ADD COLUMN effort_heavy    TEXT DEFAULT 'high';

-- Replace the stale panel guard with the round-2 fix: only role='reviewer' counts.
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
    ) < NEW.required_reviews;
END;

CREATE TRIGGER guard_no_approval_is_born_granted
BEFORE INSERT ON approvals
WHEN NEW.granted_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'guard: an approval cannot be created already granted — it must be granted through a read-back');
END;

CREATE TRIGGER guard_verdicts_cannot_be_deleted
BEFORE DELETE ON verdicts
BEGIN
    SELECT RAISE(ABORT, 'guard: a verdict is a fact on the record — it cannot be deleted');
END;

COMMIT;
