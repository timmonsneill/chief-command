-- Migration 009 — a verdict cannot be re-pointed at another version (Sol, 2026-08-28).
-- Reproduced live: UPDATE verdicts SET reviewed_version=<head> turned an approval of an
-- old version into an approval of the current one, and the job reached done.

BEGIN;
-- A verdict is about ONE version of ONE job. Sol's home-and-workers gate (2026-08-28)
-- proved that `reviewed_version` was freely updatable: approve version A, then UPDATE
-- the row to say A was B, and every completion floor is satisfied for code nobody read.
-- The verdict word was frozen; what it was ABOUT was not. Now both are.
CREATE TRIGGER IF NOT EXISTS guard_verdict_version_is_frozen
BEFORE UPDATE ON verdicts
WHEN OLD.reviewed_version IS NOT NEW.reviewed_version OR OLD.job_id <> NEW.job_id
BEGIN
    SELECT RAISE(ABORT, 'guard: a verdict cannot be moved to a different version or job');
END;

COMMIT;
