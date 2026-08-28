-- Migration 008 — bundle_kind: what a job's reviewed bundle actually IS (task #9,
-- the GO version of real code builders — candidate generation, no merge).
--
-- Before this, the panel and the gatekeeper only knew ONE shape of "the reviewed
-- work": a text answer, one file, jobs.result. A code builder's reviewed bundle is
-- a DIFF instead — a different shape needing different rules (which files are
-- allowed to appear, what "the same thing that was reviewed" means at merge time).
-- The gatekeeper has to know WHICH RULE APPLIES, and — Sol's design-gate finding —
-- that fact must be fixed at dispatch and frozen, never inferred later from a
-- seat's live `provider` (seats are re-upserted from config on every server start,
-- so a builder's provider is mutable history; its bundle_kind must not be).
--
-- 'text' = jobs.result is one file's content (today's local-model path).
-- 'diff'  = jobs.result is `git diff <merge-base main>..<tip>` (a code builder's
--           path). See harness/gatekeeper.py's merge() for how each is verified.

BEGIN;

ALTER TABLE jobs ADD COLUMN bundle_kind TEXT NOT NULL DEFAULT 'text'
    CHECK (bundle_kind IN ('text', 'diff'));

-- Frozen the same way builder_seat/tier/family already are (guard_builder_identity_
-- is_frozen): once a job exists, what SHAPE its reviewed bundle is cannot change
-- underneath the review it's collecting. Extending the existing guard — rather than
-- a second trigger watching the same table — keeps "what was this job, at birth"
-- as one check instead of two that could drift apart later.
DROP TRIGGER IF EXISTS guard_builder_identity_is_frozen;
CREATE TRIGGER guard_builder_identity_is_frozen
BEFORE UPDATE ON jobs
WHEN OLD.builder_seat <> NEW.builder_seat
  OR OLD.builder_tier <> NEW.builder_tier
  OR OLD.builder_family <> NEW.builder_family
  OR OLD.bundle_kind <> NEW.bundle_kind
BEGIN
    SELECT RAISE(ABORT, 'guard: who built this cannot be rewritten after the fact');
END;

COMMIT;
